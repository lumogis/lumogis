# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Auth browser sessions (`auth_sessions` table) — refresh rotation, reuse detection.

See LUM-29 plan. Mirrors ``mcp_tokens`` patterns (SHA-256 of full bearer,
audit via ``write_audit``, cascade revocation). Pure service layer — no FastAPI imports.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import uuid
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import NamedTuple

from actions.audit import write_audit
from models.actions import AuditEntry

import config

_log = logging.getLogger(__name__)

ACTION_SESSION_MINTED = "__session__.minted"
ACTION_SESSION_REVOKED = "__session__.revoked"
ACTION_SESSION_ADMIN_REVOKED = "__session__.admin_revoked"
ACTION_SESSION_CASCADE_REVOKED = "__session__.cascade_revoked"
ACTION_SESSION_REUSE_DETECTED = "__session__.reuse_detected"

SETTINGS_KEY_INSTANCE_SALT = "lumogis.sessions.instance_salt"

_INSTANCE_SALT_LOCK = threading.Lock()
_INSTANCE_SALT_CACHED: str | None = None


def invalidate_instance_salt_cache_for_tests() -> None:
    """Test-only helper: reset module salt cache."""
    global _INSTANCE_SALT_CACHED
    with _INSTANCE_SALT_LOCK:
        _INSTANCE_SALT_CACHED = None


def _emit_audit(
    action: str,
    *,
    user_id: str,
    input_summary: dict | None = None,
    result_summary: dict | None = None,
) -> None:
    try:
        write_audit(
            AuditEntry(
                action_name=action,
                connector="auth",
                mode="system",
                input_summary=json.dumps(input_summary or {}, default=str),
                result_summary=json.dumps(result_summary or {}, default=str),
                executed_at=datetime.now(timezone.utc),
                user_id=user_id,
            )
        )
    except Exception:
        _log.exception("auth_sessions audit write for %s failed", action)


def _hash_refresh_plaintext(refresh_jwt: str) -> str:
    return hashlib.sha256(refresh_jwt.encode("ascii")).hexdigest()


def ensure_instance_salt() -> str:
    """Return the household instance salt from ``app_settings`` (lazy-created)."""
    global _INSTANCE_SALT_CACHED
    with _INSTANCE_SALT_LOCK:
        if _INSTANCE_SALT_CACHED:
            return _INSTANCE_SALT_CACHED
        ms = config.get_metadata_store()
        row = ms.fetch_one(
            "SELECT value FROM app_settings WHERE key = %s",
            (SETTINGS_KEY_INSTANCE_SALT,),
        )
        if row and row.get("value"):
            _INSTANCE_SALT_CACHED = str(row["value"])
            return _INSTANCE_SALT_CACHED
        salt = secrets.token_hex(32)
        ms.execute(
            "INSERT INTO app_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
            (SETTINGS_KEY_INSTANCE_SALT, salt),
        )
        row2 = ms.fetch_one(
            "SELECT value FROM app_settings WHERE key = %s",
            (SETTINGS_KEY_INSTANCE_SALT,),
        )
        if not row2 or not row2.get("value"):
            raise RuntimeError("auth_sessions: could not persist instance salt")
        _INSTANCE_SALT_CACHED = str(row2["value"])
        return _INSTANCE_SALT_CACHED


def fingerprint_hashes(
    *,
    normalized_ip: str,
    user_agent: str,
    salt: str | None = None,
) -> tuple[str, str]:
    """Return ``(ip_hash, ua_hash)`` as SHA-256 hex strings."""
    eff_salt = salt if salt is not None else ensure_instance_salt()
    ip_raw = normalized_ip.encode("utf-8", errors="replace")
    ua_raw = user_agent.encode("utf-8", errors="replace")
    ip_h = hashlib.sha256(eff_salt.encode("ascii") + b"\x00" + ip_raw).hexdigest()
    ua_h = hashlib.sha256(eff_salt.encode("ascii") + b"\x00" + ua_raw).hexdigest()
    return ip_h, ua_h


_WS_RE = re.compile(r"\s+")


def compute_device_label(user_agent: str, *, utc_now: datetime) -> str:
    """§device_label: Mobile/Desktop/Unknown · YYYYMMDD-HHmm UTC."""
    raw = user_agent.strip()[:256]
    collapsed = _WS_RE.sub(" ", raw)
    ua_lower = collapsed.lower()
    mobile_markers = (
        "mobile",
        "android",
        "iphone",
        "ipad",
        "ipod",
    )
    if not collapsed:
        base = "Unknown client"
    elif any(m in ua_lower for m in mobile_markers):
        base = "Mobile"
    else:
        base = "Desktop"
    suffix = utc_now.astimezone(timezone.utc).strftime("%Y%m%d-%H%M")
    return f"{base} · {suffix}"


class AuthSessionRow(NamedTuple):
    id: str
    user_id: str
    family_id: str
    refresh_token_hash: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    revoked_at: datetime | None
    device_label: str
    ip_hash: str
    ua_hash: str


def _row_to_nt(row: dict) -> AuthSessionRow:
    return AuthSessionRow(
        id=row["id"],
        user_id=row["user_id"],
        family_id=row["family_id"],
        refresh_token_hash=row["refresh_token_hash"],
        created_at=row["created_at"],
        last_used_at=row.get("last_used_at"),
        expires_at=row["expires_at"],
        revoked_at=row.get("revoked_at"),
        device_label=row["device_label"],
        ip_hash=row["ip_hash"],
        ua_hash=row["ua_hash"],
    )


def insert_login_session(
    *,
    session_id: str,
    user_id: str,
    refresh_jwt: str,
    expires_at: datetime,
    device_label: str,
    ip_hash: str,
    ua_hash: str,
) -> str:
    """Create a fresh session row; ``family_id`` equals ``id`` (== ``session_id``) on login."""
    ms = config.get_metadata_store()
    rf_hash = _hash_refresh_plaintext(refresh_jwt)
    ms.execute(
        "INSERT INTO auth_sessions "
        "(id, user_id, family_id, refresh_token_hash, expires_at, "
        "device_label, ip_hash, ua_hash) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (session_id, user_id, session_id, rf_hash, expires_at, device_label, ip_hash, ua_hash),
    )
    _emit_audit(
        ACTION_SESSION_MINTED,
        user_id=user_id,
        input_summary={"session_id": session_id, "user_id": user_id},
        result_summary={"family_id": session_id},
    )
    return session_id


class RefreshError(Exception):
    """Invalid refresh presentation (caller maps to HTTP 401)."""


class RefreshReuseError(Exception):
    """Rotated refresh replay — family revoked; HTTP 401."""


def rotate_refresh(
    *,
    user_id: str,
    jti: str,
    refresh_jwt: str,
    refresh_ttl_seconds: int,
    utc_now: datetime | None = None,
) -> tuple[str, str, int]:
    """Validate hashed refresh row keyed by ``jti`` and rotate.

    The route must verify refresh JWT signature/expiry first. Returns
    ``(new_session_id, new_refresh_jwt, token_version_for_access)``.

    Raises:
        RefreshError: unknown JTI/hash/expired/malformed.
        RefreshReuseError: reuse detection revoked the family (audit emitted).
    """
    now = utc_now or datetime.now(timezone.utc)
    ms = config.get_metadata_store()

    row = ms.fetch_one(
        "SELECT * FROM auth_sessions WHERE id = %s AND user_id = %s",
        (jti, user_id),
    )
    if row is None:
        raise RefreshError("invalid refresh token")

    sess = _row_to_nt(row)
    presented_hash = _hash_refresh_plaintext(refresh_jwt)

    # Reuse detection: row revoked but JWT still verifies
    if sess.revoked_at is not None:
        if not hmac.compare_digest(presented_hash, sess.refresh_token_hash):
            raise RefreshError("invalid refresh token")
        _handle_reuse(ms, sess)
        raise RefreshReuseError("refresh token reused")

    if sess.expires_at < now:
        raise RefreshError("invalid refresh token")

    if not hmac.compare_digest(presented_hash, sess.refresh_token_hash):
        raise RefreshError("invalid refresh token")

    user_row = ms.fetch_one(
        "SELECT token_version FROM users WHERE id = %s",
        (user_id,),
    )
    if user_row is None:
        raise RefreshError("invalid refresh token")
    token_version = int(user_row["token_version"])

    new_id = uuid.uuid4().hex
    # Local import: avoid import cycle at module load (`auth` does not import this module).
    from auth import mint_refresh_token

    new_refresh = mint_refresh_token(user_id, new_id)
    new_hash = _hash_refresh_plaintext(new_refresh)
    new_expires = datetime.now(timezone.utc) + timedelta(seconds=refresh_ttl_seconds)

    with ms.transaction():
        ms.execute(
            "INSERT INTO auth_sessions "
            "(id, user_id, family_id, refresh_token_hash, expires_at, device_label, "
            "ip_hash, ua_hash) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                new_id,
                user_id,
                sess.family_id,
                new_hash,
                new_expires,
                sess.device_label,
                sess.ip_hash,
                sess.ua_hash,
            ),
        )
        ms.execute(
            "UPDATE auth_sessions SET revoked_at = NOW() WHERE id = %s AND revoked_at IS NULL",
            (jti,),
        )

    return new_id, new_refresh, token_version


def _handle_reuse(ms, sess: AuthSessionRow) -> None:
    """Revoke all active rows sharing ``family_id``; emit audits."""
    uid = sess.user_id
    fam = sess.family_id
    jti = sess.id

    revoked_rows: list[dict] = []
    with ms.transaction():
        rows = ms.fetch_all(
            "UPDATE auth_sessions SET revoked_at = NOW() "
            "WHERE family_id = %s AND revoked_at IS NULL RETURNING *",
            (fam,),
        )
        revoked_rows = list(rows)

    _log.warning(
        "session reuse detected user_id=%s family_id=%s presented_jti=%s",
        uid,
        fam,
        jti,
    )

    acting = uid
    try:
        _emit_audit(
            ACTION_SESSION_REUSE_DETECTED,
            user_id=acting,
            input_summary={"user_id": uid, "family_id": fam, "presented_jti": jti},
            result_summary={"revoked_session_count": len(revoked_rows)},
        )
    except Exception:
        pass

    for r in revoked_rows:
        sid = r["id"]
        try:
            _emit_audit(
                ACTION_SESSION_CASCADE_REVOKED,
                user_id=acting,
                input_summary={
                    "session_id": sid,
                    "reason": ACTION_SESSION_REUSE_DETECTED,
                },
                result_summary={"revoked_at": r.get("revoked_at")},
            )
        except Exception:
            pass


def revoke_session_for_user(*, session_id: str, user_id: str) -> AuthSessionRow | None:
    """Revoke one session if owned by ``user_id``. Returns row after revoke."""
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT * FROM auth_sessions WHERE id = %s AND user_id = %s",
        (session_id, user_id),
    )
    if row is None:
        return None
    if row.get("revoked_at"):
        return _row_to_nt(row)
    ms.execute(
        "UPDATE auth_sessions SET revoked_at = NOW() WHERE id = %s AND revoked_at IS NULL",
        (session_id,),
    )
    row2 = ms.fetch_one("SELECT * FROM auth_sessions WHERE id = %s", (session_id,))
    if row2 is None:
        return None
    acting = user_id
    try:
        _emit_audit(
            ACTION_SESSION_REVOKED,
            user_id=acting,
            input_summary={"session_id": session_id, "user_id": user_id},
            result_summary={"revoked_at": row2.get("revoked_at")},
        )
    except Exception:
        pass
    return _row_to_nt(row2)


def bump_token_version_and_revoke_all_sessions(
    *,
    user_id: str,
    cascade_actor_user_id: str,
) -> int:
    """Increment ``token_version``, revoke active ``auth_sessions`` for user.

    Returns the new ``token_version``. Caller must run inside or outside txn —
    opens **its own** transaction for atomicity."""
    ms = config.get_metadata_store()
    revoked_rows: list[dict] = []
    new_ver = 1
    with ms.transaction():
        ver_row = ms.fetch_one(
            "UPDATE users SET token_version = token_version + 1 WHERE id = %s "
            "RETURNING token_version",
            (user_id,),
        )
        if ver_row is None:
            raise RuntimeError("user row missing during token bump")
        new_ver = int(ver_row["token_version"])

        revoked_rows = ms.fetch_all(
            # SCOPE-EXEMPT: auth_sessions rows are keyed by owner user_id (no memory scope).
            "UPDATE auth_sessions SET revoked_at = NOW() "
            "WHERE user_id = %s AND revoked_at IS NULL RETURNING *",
            (user_id,),
        )

    for r in revoked_rows:
        sid = r["id"]
        try:
            _emit_audit(
                ACTION_SESSION_CASCADE_REVOKED,
                user_id=cascade_actor_user_id,
                input_summary={
                    "session_id": sid,
                    "owner_user_id": user_id,
                },
                result_summary={"revoked_at": r.get("revoked_at")},
            )
        except Exception:
            pass
    return new_ver


def list_active_sessions_for_user(user_id: str) -> list[AuthSessionRow]:
    ms = config.get_metadata_store()
    rows = ms.fetch_all(
        # SCOPE-EXEMPT: auth_sessions has no memory scope — reads are owner-scoped by user_id.
        "SELECT * FROM auth_sessions WHERE user_id = %s AND revoked_at IS NULL "
        "ORDER BY created_at DESC LIMIT 100",
        (user_id,),
    )
    return [_row_to_nt(r) for r in rows]


def list_sessions_for_admin(user_id: str) -> list[AuthSessionRow]:
    """Same as user list (admin path — router enforces admin)."""
    return list_active_sessions_for_user(user_id)


def revoke_session_admin(
    *,
    target_user_id: str,
    session_id: str,
    admin_user_id: str,
) -> AuthSessionRow | None:
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT * FROM auth_sessions WHERE id = %s AND user_id = %s",
        (session_id, target_user_id),
    )
    if row is None:
        return None
    if row.get("revoked_at"):
        return _row_to_nt(row)
    ms.execute(
        "UPDATE auth_sessions SET revoked_at = NOW() WHERE id = %s AND revoked_at IS NULL",
        (session_id,),
    )
    row2 = ms.fetch_one("SELECT * FROM auth_sessions WHERE id = %s", (session_id,))
    if row2 is None:
        return None
    try:
        _emit_audit(
            ACTION_SESSION_ADMIN_REVOKED,
            user_id=admin_user_id,
            input_summary={
                "session_id": session_id,
                "target_user_id": target_user_id,
            },
            result_summary={"revoked_at": row2.get("revoked_at")},
        )
    except Exception:
        pass
    return _row_to_nt(row2)


def revoke_all_active_in_transaction_for_user(ms, user_id: str) -> list[dict]:
    """Inside an existing caller transaction: revoke every active auth session."""
    rows = ms.fetch_all(
        # SCOPE-EXEMPT: auth_sessions has no memory scope — revocation is owner user_id only.
        "UPDATE auth_sessions SET revoked_at = NOW() "
        "WHERE user_id = %s AND revoked_at IS NULL RETURNING *",
        (user_id,),
    )
    return list(rows)


def bump_token_version_in_transaction(ms, user_id: str) -> int:
    """Inside caller transaction: bump token_version, return new value."""
    ver_row = ms.fetch_one(
        "UPDATE users SET token_version = token_version + 1 WHERE id = %s RETURNING token_version",
        (user_id,),
    )
    if ver_row is None:
        raise RuntimeError("bump_token_version_in_transaction: user missing")
    return int(ver_row["token_version"])


def emit_session_rows_cascade_audits(*, acting_user_id: str, revoked_rows: list[dict]) -> None:
    """Post-commit: one audit row per revoked session."""
    for r in revoked_rows:
        try:
            _emit_audit(
                ACTION_SESSION_CASCADE_REVOKED,
                user_id=acting_user_id,
                input_summary={
                    "session_id": r["id"],
                    "owner_user_id": r.get("user_id"),
                },
                result_summary={"revoked_at": r.get("revoked_at")},
            )
        except Exception:
            pass
