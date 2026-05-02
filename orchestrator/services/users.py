# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Users service — owns the ``users`` table.

Single responsibility: read/write the row that represents one human account
and verify their password using ``argon2-cffi``. Higher layers (routes,
middleware) get back ``InternalUser`` / ``UserPublic`` / ``UserAdminView``
from ``models.auth`` and decide what to expose.

Bi-state behaviour (see ADR ``family_lan_multi_user``):

* ``AUTH_ENABLED=false`` — single-user dev. The synthesised
  ``UserContext("default", role="admin")`` is built by ``auth.py`` and the
  table is allowed to stay empty. ``count_users()`` is still safe to call.
* ``AUTH_ENABLED=true`` — family LAN. Login is required. ``main.py``
  startup calls :func:`bootstrap_if_empty` then enforces that the table
  has at least one admin.

Argon2 details
--------------
``PasswordHasher().verify(hash, password)`` raises one of:

* :class:`argon2.exceptions.VerifyMismatchError` — wrong password.
* :class:`argon2.exceptions.InvalidHashError` — corrupted hash.
* :class:`argon2.exceptions.VerificationError` — base of both above.

Routes catch the base class and map every variant to a generic 401 to
defeat enumeration; the timing pad in ``routes/auth.py`` keeps unknown-email
latency comparable to known-email latency.
"""

from __future__ import annotations

import logging
import os
import uuid

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from models.auth import InternalUser
from models.auth import Role

import config

_log = logging.getLogger(__name__)

_ph = PasswordHasher()

MIN_PASSWORD_LENGTH = 12


class PasswordPolicyViolationError(Exception):
    """Raised when a candidate password does not meet Lumogis policy.

    Intentionally **not** a :class:`ValueError` — :func:`create_user` uses
    ``ValueError`` for duplicate-email conflicts and import paths must not
    mis-classify a policy refusal as a uniqueness race.
    """


class WrongCurrentPasswordError(Exception):
    """Raised when self-service password change fails current-password verification."""


def validate_password_policy(password: str) -> None:
    """Require minimum length (matches bootstrap admin and :class:`LoginRequest`)."""
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyViolationError(
            f"password must be at least {MIN_PASSWORD_LENGTH} characters"
        )


# Used by ``verify_credentials`` on the unknown-email path so the call still
# spends an argon2 verify worth of CPU. Pre-hashed once at import time so we
# don't pay argon2 cost on every unknown-email login.
_DUMMY_HASH = _ph.hash("user-not-found-timing-pad-please-do-not-match")


def _row_to_internal(row: dict) -> InternalUser:
    """Adapt a Postgres row dict to an :class:`InternalUser`."""
    return InternalUser(
        id=row["id"],
        email=row["email"],
        password_hash=row["password_hash"],
        role=row["role"],
        disabled=row["disabled"],
        created_at=row["created_at"],
        last_login_at=row.get("last_login_at"),
        refresh_token_jti=row.get("refresh_token_jti"),
    )


def hash_password(password: str) -> str:
    """Return an argon2id-encoded hash. Raises on impossibly-short input."""
    if not password or len(password) < 1:
        raise ValueError("password must be a non-empty string")
    return _ph.hash(password)


def create_user(
    email: str,
    password: str,
    role: Role = "user",
) -> InternalUser:
    """Insert a new user. Raises ``ValueError`` on duplicate email."""
    validate_password_policy(password)
    user_id = uuid.uuid4().hex
    pw_hash = hash_password(password)
    ms = config.get_metadata_store()
    existing = ms.fetch_one(
        "SELECT id FROM users WHERE lower(email) = lower(%s)",
        (email,),
    )
    if existing is not None:
        raise ValueError(f"email already exists: {email}")
    ms.execute(
        "INSERT INTO users (id, email, password_hash, role, disabled) "
        "VALUES (%s, %s, %s, %s, FALSE)",
        (user_id, email, pw_hash, role),
    )
    row = ms.fetch_one("SELECT * FROM users WHERE id = %s", (user_id,))
    if row is None:
        raise RuntimeError(f"create_user: row not visible after insert id={user_id}")
    return _row_to_internal(row)


def get_user_by_id(user_id: str) -> InternalUser | None:
    ms = config.get_metadata_store()
    row = ms.fetch_one("SELECT * FROM users WHERE id = %s", (user_id,))
    return _row_to_internal(row) if row else None


def get_user_by_email(email: str) -> InternalUser | None:
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT * FROM users WHERE lower(email) = lower(%s)",
        (email,),
    )
    return _row_to_internal(row) if row else None


def list_users() -> list[InternalUser]:
    ms = config.get_metadata_store()
    rows = ms.fetch_all("SELECT * FROM users ORDER BY created_at ASC")
    return [_row_to_internal(r) for r in rows]


def update_role(user_id: str, role: Role) -> InternalUser | None:
    ms = config.get_metadata_store()
    ms.execute("UPDATE users SET role = %s WHERE id = %s", (role, user_id))
    return get_user_by_id(user_id)


def set_disabled(
    user_id: str,
    disabled: bool,
    *,
    by_admin_user_id: str | None = None,
) -> InternalUser | None:
    """Disable or re-enable a user. Disabling also clears the active refresh
    jti AND cascades to revoke all active MCP tokens (per plan
    ``mcp_token_user_map`` D7).

    Atomicity contract (D7): the user-disable UPDATE and the MCP-token
    cascade revocation happen inside a single ``ms.transaction()`` block so
    a partial failure can never leave a disabled user with live MCP
    bearers. The cascade audit event is emitted by
    ``mcp_tokens.cascade_revoke_for_user`` AFTER the transaction commits.

    ``by_admin_user_id`` propagates the acting admin into the cascade audit
    so the operator can trace which admin triggered the cascade. Callers
    in this module pass ``None`` for backward compatibility (e.g.
    ``delete_user`` invokes ``set_disabled`` from the same admin context
    that already audited the delete).
    """
    ms = config.get_metadata_store()
    if disabled:
        # Local import — keep services.users boot light and avoid the
        # circular ``services.users`` ↔ ``services.mcp_tokens`` graph
        # (cascade revocation needs the same metadata store).
        from services import mcp_tokens as _mcp_tokens

        with ms.transaction():
            ms.execute(
                "UPDATE users SET disabled = TRUE, refresh_token_jti = NULL WHERE id = %s",
                (user_id,),
            )
            cascaded = _mcp_tokens.cascade_revoke_for_user(
                user_id,
                by_admin_user_id=by_admin_user_id or "",
            )
        # D14: emit one ``__mcp_token__.cascade_revoked`` audit row per
        # affected token AFTER the transaction commits. Per-token audits
        # are attributed to the *admin* who flipped the user (NOT to the
        # disabled user themselves — that would muddy the operator
        # forensics). If no admin id was threaded (legacy callers),
        # attribute to the disabled user_id so the event is still
        # reachable in the audit_log.
        actor_id = by_admin_user_id or user_id
        for tok in cascaded:
            _mcp_tokens._emit_audit(
                _mcp_tokens.ACTION_CASCADE_REVOKED,
                user_id=actor_id,
                input_summary={
                    "token_id": tok.id,
                    "owner_user_id": tok.user_id,
                    "by_admin_user_id": by_admin_user_id,
                },
                result_summary={"revoked_at": tok.revoked_at},
            )
        # Per-user connector-permission cache hygiene
        # (plan ``per_user_connector_permissions`` D5 / cache-clear-placement
        # decision). Runs AFTER the with-transaction block and AFTER the
        # cascade audit loop: a transaction rollback must NOT evict cache
        # entries for state that didn't change, and the cache-clear is
        # process-local in-memory so it cannot be transactionally coupled
        # to Postgres anyway. This is hygiene, not a request gate -- the
        # disabled-user's already-issued access JWT remains valid for
        # ACCESS_TOKEN_TTL_SECONDS regardless of cache state. Local
        # import to avoid the ``services.users`` ↔ ``permissions`` import
        # cycle at module load.
        from permissions import clear_cache_for_user

        clear_cache_for_user(user_id)
    else:
        ms.execute("UPDATE users SET disabled = FALSE WHERE id = %s", (user_id,))
        # Re-enabling does NOT clear the cache: per-user mode rows are
        # untouched by re-enable and the cache is either cold or already
        # holds DB truth.
    return get_user_by_id(user_id)


def delete_user(user_id: str) -> bool:
    """Hard-delete a user. Returns True if a row was removed.

    Per-user rows in ``connector_permissions`` and ``routine_do_tracking``
    are NOT cascaded -- they are RETAINED for forensic value (mirror
    ``mcp_token_user_map`` D7 cascade-retain). The local cache, however,
    must be cleared so that a same-id re-create does not see stale
    pre-delete entries.
    """
    ms = config.get_metadata_store()
    user = get_user_by_id(user_id)
    if user is None:
        return False
    # Cache-clear placement (plan ``per_user_connector_permissions`` D5):
    # immediately BEFORE the DELETE so that any FK-violation rollback
    # leaves a benign cold cache (next read repopulates from DB) rather
    # than a stale hot cache that grants disabled access.
    from permissions import clear_cache_for_user

    clear_cache_for_user(user_id)
    ms.execute("DELETE FROM users WHERE id = %s", (user_id,))
    return True


def count_users() -> int:
    ms = config.get_metadata_store()
    row = ms.fetch_one("SELECT COUNT(*) AS n FROM users")
    if row is None:
        return 0
    n = row.get("n") or row.get("count") or 0
    return int(n)


def count_admins() -> int:
    ms = config.get_metadata_store()
    row = ms.fetch_one("SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND disabled = FALSE")
    if row is None:
        return 0
    n = row.get("n") or row.get("count") or 0
    return int(n)


def first_admin_id() -> str | None:
    """Return the id of the oldest active admin, or None."""
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT id FROM users WHERE role = 'admin' AND disabled = FALSE "
        "ORDER BY created_at ASC LIMIT 1"
    )
    return row["id"] if row else None


def verify_credentials(email: str, password: str) -> InternalUser | None:
    """Verify ``(email, password)``. Returns the user or ``None``.

    Always pays argon2 CPU on the unknown-email path (``_DUMMY_HASH``) so
    the route's timing pad lands in the same neighbourhood as the
    known-email path. Disabled users return ``None`` (route maps both to
    a generic 401 to defeat enumeration).
    """
    user = get_user_by_email(email)
    if user is None or user.disabled:
        try:
            _ph.verify(_DUMMY_HASH, password)
        except VerificationError:
            pass
        return None
    try:
        _ph.verify(user.password_hash, password)
    except VerificationError:
        return None
    return user


def record_login(user_id: str) -> None:
    """Stamp ``last_login_at = NOW()``. Called from the route on success."""
    ms = config.get_metadata_store()
    ms.execute("UPDATE users SET last_login_at = NOW() WHERE id = %s", (user_id,))


def _apply_new_password(user_id: str, new_password: str) -> None:
    """Replace ``password_hash`` and clear ``refresh_token_jti`` for ``user_id``.

    Does **not** verify the previous password — callers must enforce authz.
    Clearing ``refresh_token_jti`` invalidates existing refresh cookies for
    that user; the current access JWT (if any) remains valid until TTL
    expiry — same limitation as :func:`set_disabled`.
    """
    validate_password_policy(new_password)
    pw_hash = hash_password(new_password)
    ms = config.get_metadata_store()
    ms.execute(
        "UPDATE users SET password_hash = %s, refresh_token_jti = NULL WHERE id = %s",
        (pw_hash, user_id),
    )


def change_own_password(user_id: str, current_password: str, new_password: str) -> None:
    """Authenticated user changes their own password.

    Raises:
        LookupError: user row missing.
        PermissionError: user disabled.
        WrongCurrentPasswordError: current password does not verify.
        PasswordPolicyViolationError: new password fails policy or equals current.
    """
    user = get_user_by_id(user_id)
    if user is None:
        raise LookupError("user not found")
    if user.disabled:
        raise PermissionError("account disabled")
    try:
        _ph.verify(user.password_hash, current_password)
    except VerificationError:
        raise WrongCurrentPasswordError()
    if new_password == current_password:
        raise PasswordPolicyViolationError("new password must differ from current password")
    _apply_new_password(user_id, new_password)


def admin_reset_user_password(target_user_id: str, new_password: str) -> None:
    """Set password for ``target_user_id`` (admin HTTP surface enforces admin role).

    The target may be disabled — login remains impossible until the account
    is re-enabled. Clears the target's refresh jti.
    """
    if get_user_by_id(target_user_id) is None:
        raise LookupError("user not found")
    _apply_new_password(target_user_id, new_password)


def cli_reset_password(
    *,
    email: str | None,
    user_id: str | None,
    new_password: str,
) -> None:
    """Local operator entrypoint — resolve by email or id and apply a new password."""
    if email and user_id:
        raise ValueError("specify exactly one of email or user_id")
    if email:
        user = get_user_by_email(email)
    elif user_id:
        user = get_user_by_id(user_id)
    else:
        raise ValueError("email or user_id is required")
    if user is None:
        raise LookupError("user not found")
    _apply_new_password(user.id, new_password)


def set_refresh_jti(user_id: str, jti: str | None) -> None:
    """Set (or clear) the active refresh jti for ``user_id``.

    Single-active-jti per user — the v1 contract. ``None`` clears it (used
    by ``/logout``, by ``set_disabled``, and by ``delete_user``).
    """
    ms = config.get_metadata_store()
    ms.execute(
        "UPDATE users SET refresh_token_jti = %s WHERE id = %s",
        (jti, user_id),
    )


def get_refresh_jti(user_id: str) -> str | None:
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT refresh_token_jti FROM users WHERE id = %s",
        (user_id,),
    )
    if row is None:
        return None
    return row.get("refresh_token_jti")


def bootstrap_if_empty() -> InternalUser | None:
    """Seed the bootstrap admin from env if the table is empty.

    Reads ``LUMOGIS_BOOTSTRAP_ADMIN_EMAIL`` and
    ``LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD``. Logs a ``CRITICAL`` warning and
    returns ``None`` when the table is empty AND either env var is unset:
    ``main.py`` is responsible for refusing to boot in that situation when
    ``AUTH_ENABLED=true``.

    Idempotent: if the table already has any user, this is a no-op.
    """
    if count_users() > 0:
        return None
    email = (os.environ.get("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL") or "").strip()
    password = os.environ.get("LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD") or ""
    if not email or not password:
        _log.critical(
            "Users table is empty and bootstrap admin env not set "
            "(LUMOGIS_BOOTSTRAP_ADMIN_EMAIL / LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD). "
            "Set both and restart, or set AUTH_ENABLED=false for single-user dev."
        )
        return None
    try:
        validate_password_policy(password)
    except PasswordPolicyViolationError:
        _log.critical(
            "LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD must be at least %s characters; "
            "refusing to bootstrap an admin with a short password.",
            MIN_PASSWORD_LENGTH,
        )
        return None
    try:
        admin = create_user(email=email, password=password, role="admin")
    except Exception as exc:
        _log.critical("Bootstrap admin creation failed: %s", exc)
        return None
    _log.info("Bootstrap admin created (id=%s, email=%s)", admin.id, admin.email)
    return admin
