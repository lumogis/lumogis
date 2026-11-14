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
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from models.auth import InternalUser
from models.auth import Role
from pydantic import EmailStr
from pydantic import TypeAdapter
from pydantic import ValidationError

import config

_log = logging.getLogger(__name__)

_ph = PasswordHasher()

MIN_PASSWORD_LENGTH = 12

_email_adapter = TypeAdapter(EmailStr)


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


def validate_user_email(email: str) -> str:
    """Return a normalised email that satisfies :class:`LoginRequest` / ``EmailStr`` rules."""
    raw = (email or "").strip()
    if not raw:
        raise ValueError("email must be non-empty")
    try:
        return str(_email_adapter.validate_python(raw))
    except ValidationError as exc:
        raise ValueError(
            "email must satisfy Lumogis auth validation (use a deliverability-valid "
            "domain such as example.com — reserved/special-use TLDs like .test are rejected)"
        ) from exc


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
        last_seen_at=row.get("last_seen_at"),
        token_version=int(row.get("token_version") or 1),
        allows_shared=bool(row.get("allows_shared", True)),
        display_name=row.get("display_name"),  # LUM-585
    )


def touch_last_seen(user_id: str, throttle_seconds: int) -> None:
    """Best-effort update of ``users.last_seen_at`` for an authenticated request.

    Single conditional UPDATE — no read-before-write, race-safe across workers,
    throttled at the DB: only writes when the stored value is NULL or older than
    ``throttle_seconds`` (LUM-334). Callers dispatch this fire-and-forget; it must
    never raise into the request path.
    """
    ms = config.get_metadata_store()
    # Prefer a dedicated short-lived connection so this background write never
    # contends with request handlers on the shared connection (LUM-334 review).
    # Plain `execute` (LUM-816): every statement now runs on its own pooled
    # connection unless the thread is mid-transaction, and this touch runs on a
    # dedicated single-worker executor (`auth.py`) that never opens one — so it
    # cannot join a caller's transaction. The old `getattr(ms,
    # "execute_isolated", ...)` fallback existed only because the shared
    # connection made a background write dangerous.
    run = ms.execute
    run(
        "UPDATE users SET last_seen_at = now() "
        "WHERE id = %s "
        "AND (last_seen_at IS NULL OR last_seen_at < now() - (%s * interval '1 second'))",
        (user_id, int(throttle_seconds)),
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
    *,
    allows_shared: bool = True,
) -> InternalUser:
    """Insert a new user. Raises ``ValueError`` on duplicate email."""
    email = validate_user_email(email)
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
        "INSERT INTO users (id, email, password_hash, role, disabled, allows_shared) "
        "VALUES (%s, %s, %s, %s, FALSE, %s)",
        (user_id, email, pw_hash, role, allows_shared),
    )
    row = ms.fetch_one("SELECT * FROM users WHERE id = %s", (user_id,))
    if row is None:
        raise RuntimeError(f"create_user: row not visible after insert id={user_id}")
    return _row_to_internal(row)


def get_user_by_id(user_id: str) -> InternalUser | None:
    ms = config.get_metadata_store()
    row = ms.fetch_one("SELECT * FROM users WHERE id = %s", (user_id,))
    return _row_to_internal(row) if row else None


def get_allows_shared(user_id: str) -> bool:
    """Return whether ``user_id`` may read household shared scope (LUM-577).

    Defaults to ``True`` when the row is missing (synthetic dev users).
    """
    ms = config.get_metadata_store()
    row = ms.fetch_one("SELECT allows_shared FROM users WHERE id = %s", (user_id,))
    if row is None:
        return True
    return bool(row.get("allows_shared", True))


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


def update_role(
    user_id: str,
    role: Role,
    *,
    by_admin_user_id: str | None = None,
) -> InternalUser | None:
    """Change a user's role (LUM-885: account-state cascade).

    * Unknown user → ``None``. Same role → no statement beyond the row lock.
    * A real demotion (``admin`` → anything else) runs the full cascade
      (MCP tokens, sessions, ``token_version``, and the invites that user
      sent), so the demoted admin's old JWT cannot re-promote them.
    * Any other change (promotion, ``user`` → ``guest``) bumps
      ``token_version`` only: the next refresh re-reads the role from the
      database, so nobody is signed out.
    """
    ms = config.get_metadata_store()
    rev: CredentialRevocation | None = None
    with ms.transaction():
        row = ms.fetch_one("SELECT role FROM users WHERE id = %s FOR UPDATE", (user_id,))
        if row is None:
            return None
        old_role = row["role"]
        if old_role == role:
            return get_user_by_id(user_id)
        ms.execute("UPDATE users SET role = %s WHERE id = %s", (role, user_id))
        if old_role == "admin" and role != "admin":
            rev = revoke_credentials_in_transaction(
                ms, user_id, revoke_invites=True, by_admin_user_id=by_admin_user_id
            )
        else:
            rev = revoke_credentials_in_transaction(
                ms,
                user_id,
                revoke_mcp=False,
                revoke_sessions=False,
                by_admin_user_id=by_admin_user_id,
            )
    finish_credential_revocation(rev)
    return get_user_by_id(user_id)


def set_display_name(user_id: str, display_name: str | None) -> InternalUser | None:
    """Set (or clear) a user's admin-managed display name (LUM-585).

    Empty or whitespace-only normalizes to ``NULL`` so the read-time label
    falls back to the email local-part and the stored value is always either a
    non-empty trimmed string or ``NULL``.
    """
    normalized = (display_name or "").strip() or None
    ms = config.get_metadata_store()
    ms.execute("UPDATE users SET display_name = %s WHERE id = %s", (normalized, user_id))
    return get_user_by_id(user_id)


def display_label_for(user_id: str) -> str | None:
    """Resolve a user id to their attribution label, or ``None`` (LUM-585).

    Precedence: the admin-set ``display_name`` (when non-empty) → the email
    local-part (``tommstar`` from ``tommstar@pm.me``) → ``None``. Returns the
    label only, **never** the full email. ``None`` (missing/deleted user, the
    synthetic single-user ``default``, or no email) makes the caller fall back
    to the generic "Shared with your household" indicator.
    """
    # The synthetic single-user dev id (AUTH_ENABLED=false) has no household to
    # attribute to; kept as a literal to avoid the auth<->users import cycle.
    if not user_id or user_id == "default":
        return None
    user = get_user_by_id(user_id)
    if user is None:
        return None
    if user.display_name and user.display_name.strip():
        return user.display_name.strip()
    email = (user.email or "").strip()
    if "@" in email:
        local = email.split("@", 1)[0]
        return local or None
    return None


@dataclass(frozen=True)
class CredentialRevocation:
    """What :func:`revoke_credentials_in_transaction` revoked, for the post-commit half."""

    user_id: str
    by_admin_user_id: str | None
    mcp_tokens: list = field(default_factory=list)  # list[InternalMcpToken]
    sessions: list = field(default_factory=list)  # auth_sessions rows (id, user_id, revoked_at)
    invite_ids: list = field(default_factory=list)  # list[str]

    @property
    def actor_user_id(self) -> str:
        return self.by_admin_user_id or self.user_id


def revoke_credentials_in_transaction(
    ms,
    user_id: str,
    *,
    revoke_mcp: bool = True,
    revoke_sessions: bool = True,
    revoke_invites: bool = False,
    by_admin_user_id: str | None = None,
) -> CredentialRevocation:
    """The one door for revoking what a user holds (LUM-885).

    **Any account-state change that must end a user's access calls this plus**
    :func:`finish_credential_revocation` (or :func:`revoke_all_credentials`).
    Writing another inline "bump + revoke" copy reintroduces the drift behind
    security audit #2 ID-001. Credential kinds covered: access JWTs (via
    ``token_version``), refresh sessions, ``lmcp_`` MCP tokens, and (opt-in)
    the unredeemed invites the user minted. A new kind a user can hold must be
    added here and checked against its owner at use.

    The caller MUST already hold an open transaction. ``ms`` is passed only to
    the two ``auth_sessions`` in-transaction helpers; ``cascade_revoke_for_user``
    and the invite revoke call ``config.get_metadata_store()`` and join the
    caller's transaction **by thread affinity** (LUM-816,
    ``postgres_store.py``; ``isolated_transaction()`` yields ``self``). A change
    to that affinity (LUM-822) must re-run the live atomicity tests.

    Order matters (D9): the ``token_version`` bump runs first so the ``users``
    row lock is taken before anything else is revoked.
    """
    # Local imports keep ``services.users`` light at module load and avoid the
    # ``users`` <-> ``mcp_tokens`` <-> ``auth`` import cycles. Each step is
    # called through its module so tests can patch it.
    from services import auth_sessions as _auth_sess
    from services import mcp_tokens as _mcp_tokens
    from services import user_invites as _user_invites

    _auth_sess.bump_token_version_in_transaction(ms, user_id)
    mcp_tokens: list = []
    sessions: list = []
    invite_ids: list = []
    if revoke_mcp:
        mcp_tokens = _mcp_tokens.cascade_revoke_for_user(
            user_id, by_admin_user_id=by_admin_user_id or ""
        )
    if revoke_sessions:
        sessions = _auth_sess.revoke_all_active_in_transaction_for_user(ms, user_id)
    if revoke_invites:
        invite_ids = _user_invites.revoke_unredeemed_for_creator_in_transaction(user_id)
    return CredentialRevocation(
        user_id=user_id,
        by_admin_user_id=by_admin_user_id,
        mcp_tokens=list(mcp_tokens),
        sessions=list(sessions),
        invite_ids=list(invite_ids),
    )


def finish_credential_revocation(
    rev: CredentialRevocation,
    *,
    clear_permission_cache: bool = False,
) -> None:
    """Post-commit half of :func:`revoke_credentials_in_transaction`. Never raises.

    Caches first, so a failing audit cannot leave a stale cache; each step is
    isolated so one failure cannot skip the rest or turn a committed change
    into a 500. Audits are attributed to the acting admin when there is one,
    otherwise to the user (``set_disabled`` D14 rule, extended).
    """
    from auth import invalidate_sid_cache
    from auth import invalidate_token_version_cache

    from services import auth_sessions as _auth_sess
    from services import mcp_tokens as _mcp_tokens
    from services import user_invites as _user_invites

    for r in rev.sessions:
        try:
            invalidate_sid_cache(r["id"])
        except Exception:
            _log.exception("finish_credential_revocation: sid cache invalidation failed")
    try:
        invalidate_token_version_cache(rev.user_id)
    except Exception:
        _log.exception("finish_credential_revocation: token_version cache invalidation failed")
    if clear_permission_cache:
        try:
            # Per-user connector-permission cache hygiene (process-local, so it
            # runs after the commit, never before). Local import avoids the
            # ``users`` <-> ``permissions`` cycle.
            from permissions import clear_cache_for_user

            clear_cache_for_user(rev.user_id)
        except Exception:
            _log.exception("finish_credential_revocation: permission cache clear failed")

    actor = rev.actor_user_id
    for tok in rev.mcp_tokens:
        try:
            _mcp_tokens._emit_audit(
                _mcp_tokens.ACTION_CASCADE_REVOKED,
                user_id=actor,
                input_summary={
                    "token_id": tok.id,
                    "owner_user_id": tok.user_id,
                    "by_admin_user_id": rev.by_admin_user_id,
                },
                result_summary={"revoked_at": tok.revoked_at},
            )
        except Exception:
            _log.exception("finish_credential_revocation: mcp cascade audit failed")
    try:
        _auth_sess.emit_session_rows_cascade_audits(acting_user_id=actor, revoked_rows=rev.sessions)
    except Exception:
        _log.exception("finish_credential_revocation: session cascade audit failed")
    for invite_id in rev.invite_ids:
        try:
            _user_invites._emit_audit(
                "__user_invite__.revoked",
                user_id=actor,
                input_summary={
                    "invite_id": invite_id,
                    "creator_user_id": rev.user_id,
                    "cascade": True,
                },
                result_summary={"ok": True},
            )
        except Exception:
            _log.exception("finish_credential_revocation: invite cascade audit failed")


def revoke_all_credentials(
    user_id: str,
    *,
    by_admin_user_id: str | None = None,
    revoke_mcp: bool = True,
    revoke_sessions: bool = True,
    clear_permission_cache: bool = False,
) -> CredentialRevocation:
    """Revoke everything in one transaction, then run the post-commit half.

    Used by "log out everywhere". Invites are left alone: they are a household
    object, and a member's pending invites should not die on a sign-out.
    """
    ms = config.get_metadata_store()
    with ms.transaction():
        rev = revoke_credentials_in_transaction(
            ms,
            user_id,
            revoke_mcp=revoke_mcp,
            revoke_sessions=revoke_sessions,
            by_admin_user_id=by_admin_user_id,
        )
    finish_credential_revocation(rev, clear_permission_cache=clear_permission_cache)
    return rev


def set_disabled(
    user_id: str,
    disabled: bool,
    *,
    by_admin_user_id: str | None = None,
) -> InternalUser | None:
    """Disable or re-enable a user. Disabling revokes everything the user holds
    (MCP tokens, auth sessions, ``token_version``, and the invites they sent)
    through :func:`revoke_credentials_in_transaction`.

    Atomicity contract (D7): the revocation and the ``disabled`` flag commit in
    one ``ms.transaction()``, so a partial failure can never leave a disabled
    user with live MCP bearers. Audits and cache clears run after the commit.

    ``by_admin_user_id`` is the acting admin; the cascade audits are attributed
    to them. ``delete_user`` does not call this function; it uses the same
    helper. Re-enabling does not un-revoke anything (an admin re-mints invites).
    """
    ms = config.get_metadata_store()
    if disabled:
        with ms.transaction():
            rev = revoke_credentials_in_transaction(
                ms, user_id, revoke_invites=True, by_admin_user_id=by_admin_user_id
            )
            ms.execute("UPDATE users SET disabled = TRUE WHERE id = %s", (user_id,))
        finish_credential_revocation(rev, clear_permission_cache=True)
    else:
        ms.execute("UPDATE users SET disabled = FALSE WHERE id = %s", (user_id,))
        # Re-enabling does NOT clear the cache: per-user mode rows are
        # untouched by re-enable and the cache is either cold or already
        # holds DB truth.
    return get_user_by_id(user_id)


def delete_user(user_id: str, *, by_admin_user_id: str | None = None) -> bool:
    """Hard-delete a user. Returns True if a row was removed.

    MCP tokens are revoked and **retained** (ADR 017 forensic rows);
    unredeemed invites the user sent are revoked and kept. Per-user rows in
    ``connector_permissions`` and ``routine_do_tracking`` are NOT cascaded --
    they are RETAINED for forensic value. The local cache, however, must be
    cleared so that a same-id re-create does not see stale pre-delete entries.
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
    from auth import invalidate_sid_cache

    # SCOPE-EXEMPT: auth_sessions has no memory scope — owner-scoped by user_id
    # (sid LRU hygiene). Every session id, revoked or not.
    sess_rows = ms.fetch_all("SELECT id FROM auth_sessions WHERE user_id = %s", (user_id,))
    with ms.transaction():
        locked = ms.fetch_one("SELECT id FROM users WHERE id = %s FOR UPDATE", (user_id,))
        if locked is None:
            # A concurrent delete won; the route keeps its 404.
            return False
        rev = revoke_credentials_in_transaction(
            ms, user_id, revoke_invites=True, by_admin_user_id=by_admin_user_id
        )
        # SCOPE-EXEMPT: auth_sessions rows are keyed by user_id only (purge on user delete).
        ms.execute("DELETE FROM auth_sessions WHERE user_id = %s", (user_id,))
        ms.execute("DELETE FROM users WHERE id = %s", (user_id,))
    finish_credential_revocation(rev, clear_permission_cache=True)
    for row in sess_rows:
        try:
            invalidate_sid_cache(row["id"])
        except Exception:
            _log.exception("delete_user: sid cache invalidation failed")
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


def list_admin_ids() -> list[str]:
    """Ids of every active admin, oldest first.

    LUM-758: household-wide audit notices (e.g. the egress allowlist widening) go
    to all admins, not just the first — ``first_admin_id`` would silently make the
    notice depend on who happened to be created earliest.
    """
    ms = config.get_metadata_store()
    rows = ms.fetch_all(
        "SELECT id FROM users WHERE role = 'admin' AND disabled = FALSE ORDER BY created_at ASC"
    )
    return [row["id"] for row in rows or []]


def first_admin_id() -> str | None:
    """Return the id of the oldest active admin, or None."""
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT id FROM users WHERE role = 'admin' AND disabled = FALSE "
        "ORDER BY created_at ASC LIMIT 1"
    )
    return row["id"] if row else None


def spend_dummy_verify(password: str | None = None) -> None:
    """Burn one argon2 verify against ``_DUMMY_HASH``.

    The single timing pad for every unknown-subject branch, so the
    password-reset request path pays the same CPU on its unknown-address branch
    as the login path does on its unknown-email one.

    This claimed to be "extracted from `verify_credentials`" while
    `verify_credentials` kept its own inline copy — so the helper added a
    *second* pad rather than removing one, which is the exact drift the
    docstring said it prevented. `verify_credentials` now calls it.

    ``password`` exists to preserve that call site exactly: login verifies
    against the caller's real password, the reset path against a fixed string
    (it has no password to offer). Both are argon2 verifies of the same cost;
    the parameter keeps the difference explicit rather than quietly changing
    the login path's behaviour while unifying the code.
    """
    try:
        _ph.verify(
            _DUMMY_HASH,
            password
            if password is not None
            else "user-not-found-timing-pad-please-do-not-match-either",
        )
    except VerificationError:
        pass


def verify_credentials(email: str, password: str) -> InternalUser | None:
    """Verify ``(email, password)``. Returns the user or ``None``.

    Always pays argon2 CPU on the unknown-email path (``_DUMMY_HASH``) so
    the route's timing pad lands in the same neighbourhood as the
    known-email path. Disabled users return ``None`` (route maps both to
    a generic 401 to defeat enumeration).
    """
    user = get_user_by_email(email)
    if user is None or user.disabled:
        spend_dummy_verify(password)
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


def _apply_new_password(
    user_id: str,
    new_password: str,
    *,
    by_admin_user_id: str | None = None,
) -> None:
    """Replace ``password_hash`` and revoke everything the user holds.

    Revokes MCP tokens, auth sessions and bumps ``token_version`` through
    :func:`revoke_credentials_in_transaction` (LUM-885). Callers must enforce
    authz. Post-commit: clears the in-process caches and writes the audits.

    Also consumes any outstanding forgot-password token for the user (LUM-65).
    This lives here rather than in each caller so every existing path inherits
    it — admin reset, CLI ``scripts.reset_password``, self-service change, and
    the reset redeem itself. Otherwise the realistic remediation sequence (a
    member requests an email reset, gets impatient, an admin resets the
    password directly) would leave a live, unexpired link in a mail archive
    pointed at the account that was just remediated.
    """
    ms = config.get_metadata_store()
    with ms.isolated_transaction() as tx:
        rev = apply_new_password_in_transaction(
            tx, user_id, new_password, by_admin_user_id=by_admin_user_id
        )
    finish_password_change(rev)


def apply_new_password_in_transaction(
    tx,
    user_id: str,
    new_password: str,
    *,
    by_admin_user_id: str | None = None,
) -> CredentialRevocation:
    """The DB half of a password change, run inside the caller's transaction.

    Split out (LUM-65) so a caller that must change the password **atomically
    with something else** — the reset redeem, which has to consume its token in
    the same unit of work — can do so without nesting transactions. Returns the
    :class:`CredentialRevocation`; the caller must pass it to
    :func:`finish_password_change` **after** the commit, because cache
    invalidation and audit writes must not happen for a change that rolled back.

    Revokes MCP tokens on every path (LUM-885), as well as the sessions, and
    bumps ``token_version`` (the helper owns the bump; the password ``UPDATE``
    does not).

    Also consumes any outstanding forgot-password token for the user. This lives
    here rather than in each caller so every path inherits it — admin reset, CLI
    ``scripts.reset_password``, self-service change, and the reset redeem
    itself. Otherwise the realistic remediation sequence (a member requests an
    email reset, gets impatient, an admin resets the password directly) would
    leave a live, unexpired link in a mail archive pointed at the account that
    was just remediated.
    """
    from services import password_reset as _password_reset

    validate_password_policy(new_password)
    pw_hash = hash_password(new_password)
    tx.execute(
        "UPDATE users SET password_hash = %s WHERE id = %s",
        (pw_hash, user_id),
    )
    _password_reset.invalidate_for_user(tx, user_id)
    return revoke_credentials_in_transaction(tx, user_id, by_admin_user_id=by_admin_user_id)


def finish_password_change(rev: CredentialRevocation) -> None:
    """Post-commit side effects of a password change: caches, then audits."""
    finish_credential_revocation(rev)


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


def admin_reset_user_password(
    target_user_id: str,
    new_password: str,
    *,
    by_admin_user_id: str | None = None,
) -> None:
    """Set password for ``target_user_id`` (admin HTTP surface enforces admin role).

    The target may be disabled — login remains impossible until the account
    is re-enabled. Revokes the target's sessions and MCP tokens.
    """
    if get_user_by_id(target_user_id) is None:
        raise LookupError("user not found")
    _apply_new_password(target_user_id, new_password, by_admin_user_id=by_admin_user_id)


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


def get_onboarding_completed_at(user_id: str) -> datetime | None:
    """Return stored onboarding completion instant, or ``None`` if not completed.

    Caller must enforce authz. When ``AUTH_ENABLED=false`` the route layer
    synthesises a response and does not hit this helper.
    """
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT onboarding_completed_at FROM users WHERE id = %s",
        (user_id,),
    )
    if row is None:
        return None
    val = row.get("onboarding_completed_at")
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val
    return None


def set_onboarding_completed(user_id: str) -> datetime:
    """Idempotently set ``onboarding_completed_at`` if unset; return persisted value.

    Raises:
        LookupError: no ``users`` row for ``user_id``.
    """
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "UPDATE users SET onboarding_completed_at = COALESCE(onboarding_completed_at, NOW()) "
        "WHERE id = %s RETURNING onboarding_completed_at",
        (user_id,),
    )
    if row is None:
        raise LookupError("user not found")
    val = row.get("onboarding_completed_at")
    if not isinstance(val, datetime):
        raise RuntimeError("set_onboarding_completed: unexpected RETURNING shape")
    if val.tzinfo is None:
        return val.replace(tzinfo=timezone.utc)
    return val


def get_wow_dismissed_at(user_id: str) -> datetime | None:
    """Return stored wow-path dismissal instant, or ``None`` if not dismissed."""
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT wow_dismissed_at FROM users WHERE id = %s",
        (user_id,),
    )
    if row is None:
        return None
    val = row.get("wow_dismissed_at")
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val
    return None


def set_wow_dismissed(user_id: str) -> datetime:
    """Idempotently set ``wow_dismissed_at`` if unset; return persisted value.

    Raises:
        LookupError: no ``users`` row for ``user_id``.
    """
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "UPDATE users SET wow_dismissed_at = COALESCE(wow_dismissed_at, NOW()) "
        "WHERE id = %s RETURNING wow_dismissed_at",
        (user_id,),
    )
    if row is None:
        raise LookupError("user not found")
    val = row.get("wow_dismissed_at")
    if not isinstance(val, datetime):
        raise RuntimeError("set_wow_dismissed: unexpected RETURNING shape")
    if val.tzinfo is None:
        return val.replace(tzinfo=timezone.utc)
    return val


def _read_bootstrap_admin_password() -> str:
    """Resolve the bootstrap-admin password, preferring a 0600 file over env.

    LUM-548: the native server passes the password via
    ``LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD_FILE`` (a 0600 file it writes, then we
    unlink) so the secret never transits the process environment
    (``/proc/{pid}/environ``) nor is inherited by child processes. Docker/CI
    still set ``LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD`` directly; that raw env var
    remains the fallback. Precedence: ``_FILE`` wins when it resolves.

    The file is opened with ``O_NOFOLLOW`` so a symlink at the path is refused
    atomically (no ``is_file()``/``is_symlink()`` TOCTOU window); its contents
    are read **verbatim** (no strip — a trailing space is a valid password
    character) and the file is unlinked immediately after read. Any failure
    (missing, symlinked, unreadable) logs without the value and falls through
    to the env var — a transport error never crashes the boot.
    """
    file_path = (os.environ.get("LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD_FILE") or "").strip()
    if file_path:
        try:
            # O_NOFOLLOW → ELOOP if the final component is a symlink. The path is
            # set by the trusted supervisor; this is defence-in-depth. O_NOFOLLOW
            # is Unix-only — on Windows (LUM-474) it is absent, so degrade to no
            # flag (mirrors write_private's non-unix branch; the /proc/environ
            # threat this hardens against is itself Unix-only).
            nofollow = getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(file_path, os.O_RDONLY | nofollow)
        except OSError as exc:
            _log.warning(
                "bootstrap password file present but unreadable/symlinked (%s) "
                "— ignoring and falling back to env",
                exc.__class__.__name__,
            )
        else:
            try:
                with os.fdopen(fd, "r", encoding="utf-8") as fh:
                    return fh.read()  # verbatim — never strip (LUM-548)
            except Exception:  # noqa: BLE001 — never crash the boot on a read error
                _log.critical("failed to read bootstrap password file — ignoring")
            finally:
                try:
                    os.unlink(file_path)
                except OSError:
                    _log.debug("could not unlink bootstrap password file")
    return os.environ.get("LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD") or ""


def bootstrap_if_empty() -> InternalUser | None:
    """Seed the bootstrap admin from env/file if the table is empty.

    Reads ``LUMOGIS_BOOTSTRAP_ADMIN_EMAIL`` and the password via
    :func:`_read_bootstrap_admin_password` — which prefers a 0600 file named
    by ``LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD_FILE`` (native server) and falls back
    to the raw ``LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD`` env var (Docker/CI). Logs a
    ``CRITICAL`` warning and returns ``None`` when the table is empty AND no
    password/email is available: ``main.py`` is responsible for refusing to
    boot in that situation when ``AUTH_ENABLED=true``.

    Idempotent: if the table already has any user, this is a no-op.
    """
    if count_users() > 0:
        return None
    email = (os.environ.get("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL") or "").strip()
    password = _read_bootstrap_admin_password()
    if not email or not password:
        _log.critical(
            "Users table is empty and bootstrap admin credentials not set "
            "(LUMOGIS_BOOTSTRAP_ADMIN_EMAIL + LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD "
            "or LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD_FILE). Set them and restart, "
            "or set AUTH_ENABLED=false for single-user dev."
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
        email = validate_user_email(email)
    except ValueError as exc:
        _log.critical(
            "LUMOGIS_BOOTSTRAP_ADMIN_EMAIL is set but invalid for auth: %s",
            exc,
        )
        return None
    try:
        admin = create_user(email=email, password=password, role="admin")
    except Exception as exc:
        _log.critical("Bootstrap admin creation failed: %s", exc)
        return None
    _log.info("Bootstrap admin created (id=%s, email=%s)", admin.id, admin.email)
    return admin
