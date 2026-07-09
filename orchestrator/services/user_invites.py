# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Household invite service — mint, peek, redeem, revoke, list (LUM-186)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from urllib.parse import quote

from actions.audit import write_audit
from models.actions import AuditEntry
from models.auth import InternalUser
from models.auth import Role
from models.user_invite import DuplicateEmailError
from models.user_invite import EmailPolicyViolationError
from models.user_invite import InternalInvite
from models.user_invite import InviteAdminRow
from models.user_invite import InviteInvalidError
from models.user_invite import InvitePeekPublic

import config
from services import users as users_service

_log = logging.getLogger(__name__)

_TOKEN_PREFIX_TAG = "linv_"
_TOKEN_BODY_BYTES = 28
_TOKEN_BODY_LEN = 45
_LOOKUP_PREFIX_LEN = 16
_MINT_COLLISION_BUDGET = 5


def _default_ttl_hours() -> int:
    raw = os.environ.get("LUMOGIS_INVITE_TTL_HOURS", "48").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 48


def _row_to_internal(row: dict) -> InternalInvite:
    return InternalInvite(
        id=row["id"],
        token_prefix=row["token_prefix"],
        token_hash=row["token_hash"],
        role=row["role"],
        allows_shared=bool(row["allows_shared"]),
        created_by=row["created_by"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        used_at=row.get("used_at"),
        used_by=row.get("used_by"),
        revoked_at=row.get("revoked_at"),
    )


def _generate_plaintext() -> tuple[str, str]:
    raw = secrets.token_bytes(_TOKEN_BODY_BYTES)
    body = base64.b32encode(raw).decode("ascii").rstrip("=").lower()
    plaintext = _TOKEN_PREFIX_TAG + body
    return plaintext, body[:_LOOKUP_PREFIX_LEN]


def _hash_plaintext(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("ascii")).hexdigest()


def _parse_plaintext(plaintext: str) -> tuple[str, str] | None:
    if not isinstance(plaintext, str) or not plaintext.startswith(_TOKEN_PREFIX_TAG):
        return None
    body = plaintext[len(_TOKEN_PREFIX_TAG) :]
    if len(body) < _LOOKUP_PREFIX_LEN:
        return None
    token_prefix = body[:_LOOKUP_PREFIX_LEN]
    return token_prefix, _hash_plaintext(plaintext)


def _lookup_active_by_prefix(token_prefix: str) -> dict | None:
    ms = config.get_metadata_store()
    return ms.fetch_one(
        "SELECT * FROM user_invites "
        "WHERE token_prefix = %s AND used_at IS NULL AND revoked_at IS NULL "
        "AND expires_at > NOW()",
        (token_prefix,),
    )


def _verify_row(plaintext: str, row: dict) -> bool:
    presented_hash = _hash_plaintext(plaintext)
    return hmac.compare_digest(presented_hash, row["token_hash"])


def build_invite_url(plaintext: str) -> str:
    origin = os.environ.get("LUMOGIS_PUBLIC_ORIGIN", "").strip().rstrip("/")
    rel = f"/invite?token={quote(plaintext, safe='')}"
    if origin:
        return f"{origin}{rel}"
    return rel


def _emit_audit(
    action: str,
    *,
    user_id: str,
    input_summary: dict | None = None,
    result_summary: dict | None = None,
) -> None:
    """Write a single ``audit_log`` row (LUM-579); failures are logged only."""
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
        _log.exception("audit write for %s failed", action)


def mint_invite(*, role: Role, allows_shared: bool, created_by: str) -> tuple[InternalInvite, str]:
    ms = config.get_metadata_store()
    ttl = timedelta(hours=_default_ttl_hours())
    expires_at = datetime.now(timezone.utc) + ttl
    last_exc: Exception | None = None
    for attempt in range(_MINT_COLLISION_BUDGET):
        plaintext, token_prefix = _generate_plaintext()
        token_hash = _hash_plaintext(plaintext)
        invite_id = uuid.uuid4().hex
        try:
            ms.execute(
                "INSERT INTO user_invites "
                "(id, token_prefix, token_hash, role, allows_shared, created_by, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (invite_id, token_prefix, token_hash, role, allows_shared, created_by, expires_at),
            )
        except Exception as exc:
            last_exc = exc
            msg = str(exc).lower()
            if "user_invites_active_prefix_uniq" in msg or "unique" in msg:
                _log.info(
                    "invite mint prefix collision on attempt %d/%d; regenerating",
                    attempt + 1,
                    _MINT_COLLISION_BUDGET,
                )
                continue
            raise
        row = ms.fetch_one("SELECT * FROM user_invites WHERE id = %s", (invite_id,))
        if row is None:
            raise RuntimeError(f"invite mint: row not visible after insert id={invite_id}")
        internal = _row_to_internal(row)
        _emit_audit(
            "__user_invite__.minted",
            user_id=created_by,
            input_summary={"role": role, "allows_shared": allows_shared},
            result_summary={"invite_id": internal.id, "token_prefix": internal.token_prefix},
        )
        return internal, plaintext
    raise RuntimeError(
        "invite mint collision retry budget exhausted "
        f"({_MINT_COLLISION_BUDGET}); last: {last_exc!r}"
    )


def peek_invite(plaintext: str) -> InvitePeekPublic | None:
    parsed = _parse_plaintext(plaintext)
    if parsed is None:
        return None
    token_prefix, _ = parsed
    row = _lookup_active_by_prefix(token_prefix)
    if row is None or not _verify_row(plaintext, row):
        return None
    return InvitePeekPublic(
        allows_shared=bool(row["allows_shared"]),
        expires_at=row["expires_at"],
    )


@dataclass(frozen=True)
class RedeemResult:
    user: InternalUser
    allows_shared: bool
    role: Role


def redeem_invite(plaintext: str, email: str, password: str) -> RedeemResult:
    parsed = _parse_plaintext(plaintext)
    if parsed is None:
        raise InviteInvalidError()
    token_prefix, _ = parsed
    row = _lookup_active_by_prefix(token_prefix)
    if row is None or not _verify_row(plaintext, row):
        raise InviteInvalidError()

    try:
        normalised_email = users_service.validate_user_email(email)
    except ValueError as exc:
        raise EmailPolicyViolationError(str(exc)) from exc

    try:
        users_service.validate_password_policy(password)
    except users_service.PasswordPolicyViolationError as exc:
        raise exc

    pw_hash = users_service.hash_password(password)
    invite_id = row["id"]
    invite_role: Role = row["role"]  # type: ignore[assignment]
    allows_shared = bool(row["allows_shared"])
    user_id = uuid.uuid4().hex

    ms = config.get_metadata_store()
    with ms.transaction():
        existing = ms.fetch_one(
            "SELECT id FROM users WHERE lower(email) = lower(%s)",
            (normalised_email,),
        )
        if existing is not None:
            raise DuplicateEmailError()

        consumed = ms.fetch_one(
            "UPDATE user_invites SET used_at = NOW(), used_by = %s "
            "WHERE id = %s AND used_at IS NULL AND revoked_at IS NULL "
            "AND expires_at > NOW() "
            "RETURNING *",
            (user_id, invite_id),
        )
        if consumed is None:
            raise InviteInvalidError()

        try:
            ms.execute(
                "INSERT INTO users (id, email, password_hash, role, disabled, allows_shared) "
                "VALUES (%s, %s, %s, %s, FALSE, %s)",
                (user_id, normalised_email, pw_hash, invite_role, allows_shared),
            )
        except Exception as exc:
            msg = str(exc).lower()
            if "unique" in msg or "duplicate" in msg:
                raise DuplicateEmailError() from exc
            raise

    user_row = ms.fetch_one("SELECT * FROM users WHERE id = %s", (user_id,))
    if user_row is None:
        raise RuntimeError(f"redeem_invite: user row missing after insert id={user_id}")
    _emit_audit(
        "__user_invite__.redeemed",
        user_id=user_id,
        input_summary={"invite_id": invite_id},
        result_summary={
            "email": normalised_email,
            "role": invite_role,
            "allows_shared": allows_shared,
        },
    )
    return RedeemResult(
        user=users_service._row_to_internal(user_row),
        allows_shared=allows_shared,
        role=invite_role,
    )


def revoke_invite(invite_id: str, *, actor_admin_id: str) -> bool:
    ms = config.get_metadata_store()
    before = ms.fetch_one(
        "SELECT id FROM user_invites WHERE id = %s AND used_at IS NULL AND revoked_at IS NULL",
        (invite_id,),
    )
    if before is None:
        return False
    ms.execute(
        "UPDATE user_invites SET revoked_at = NOW() "
        "WHERE id = %s AND used_at IS NULL AND revoked_at IS NULL",
        (invite_id,),
    )
    _log.info("admin: revoked invite id=%s by=%s", invite_id, actor_admin_id)
    _emit_audit(
        "__user_invite__.revoked",
        user_id=actor_admin_id,
        input_summary={"invite_id": invite_id},
        result_summary={"ok": True},
    )
    return True


def list_invites(*, include_consumed: bool = True) -> list[InternalInvite]:
    ms = config.get_metadata_store()
    if include_consumed:
        rows = ms.fetch_all("SELECT * FROM user_invites ORDER BY created_at DESC")
    else:
        rows = ms.fetch_all(
            "SELECT * FROM user_invites "
            "WHERE used_at IS NULL AND revoked_at IS NULL AND expires_at > NOW() "
            "ORDER BY created_at DESC",
        )
    return [_row_to_internal(r) for r in rows]


def to_admin_row(internal: InternalInvite) -> InviteAdminRow:
    return InviteAdminRow(
        id=internal.id,
        role=internal.role,
        allows_shared=internal.allows_shared,
        created_by=internal.created_by,
        created_at=internal.created_at,
        expires_at=internal.expires_at,
        used_at=internal.used_at,
        used_by=internal.used_by,
        revoked_at=internal.revoked_at,
        token_prefix=internal.token_prefix,
    )
