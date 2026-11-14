# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Forgot-password reset tokens — mint, redeem, invalidate (LUM-65).

Deliberately shaped like :mod:`services.user_invites`, the household's other
public unauthenticated token: same entropy, same 16-character lookup prefix,
same SHA-256-at-rest with :func:`hmac.compare_digest` on verify, same
collision budget, same atomic single-statement consume. The prefix tag differs
(``prst_`` vs ``linv_``) so a token can never be presented to the wrong redeem
endpoint and get a partially-meaningful answer.

Two invalidation paths, not one:

* minting for a user marks that user's older unused tokens used, so only the
  newest emailed link works; and
* :func:`invalidate_for_user` is called from
  :func:`services.users._apply_new_password`, so an admin reset, a CLI reset,
  or a self-service change also kills any live emailed link. Without that
  second path the product's two remediation routes would not know about each
  other: an admin "fixes" a locked-out account from Admin → Users while an
  unexpired ``prst_`` link is still sitting in a mail archive pointed at it.
"""

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

from actions.audit import write_audit
from models.actions import AuditEntry

import config

_log = logging.getLogger(__name__)

# Pinned to mirror services.user_invites — see module docstring.
_TOKEN_PREFIX_TAG = "prst_"
_TOKEN_BODY_BYTES = 28
_LOOKUP_PREFIX_LEN = 16
_MINT_COLLISION_BUDGET = 5

_DEFAULT_TTL_MINUTES = 30
_TTL_MIN_MINUTES = 5
_TTL_MAX_MINUTES = 120


class ResetTokenInvalidError(Exception):
    """Token is unknown, malformed, expired, or already consumed."""


def ttl_minutes() -> int:
    """Token lifetime, clamped to [5, 120] minutes.

    Clamped rather than validated-and-raised: a typo in an operator's env must
    not make password recovery unavailable, which is the one feature whose job
    is to work when everything else about the account has gone wrong.
    """
    raw = os.environ.get("LUMOGIS_PASSWORD_RESET_TTL_MINUTES", "").strip()
    if not raw:
        return _DEFAULT_TTL_MINUTES
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_TTL_MINUTES
    return max(_TTL_MIN_MINUTES, min(_TTL_MAX_MINUTES, value))


def _generate_plaintext() -> tuple[str, str]:
    raw = secrets.token_bytes(_TOKEN_BODY_BYTES)
    body = base64.b32encode(raw).decode("ascii").rstrip("=").lower()
    return _TOKEN_PREFIX_TAG + body, body[:_LOOKUP_PREFIX_LEN]


def _hash_plaintext(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("ascii")).hexdigest()


def _parse_plaintext(plaintext: str) -> str | None:
    """Return the lookup prefix for a well-formed token, else ``None``."""
    if not isinstance(plaintext, str) or not plaintext.startswith(_TOKEN_PREFIX_TAG):
        return None
    body = plaintext[len(_TOKEN_PREFIX_TAG) :]
    if len(body) < _LOOKUP_PREFIX_LEN:
        return None
    return body[:_LOOKUP_PREFIX_LEN]


def _emit_audit(
    action: str,
    *,
    user_id: str,
    input_summary: dict | None = None,
    result_summary: dict | None = None,
) -> None:
    """Write one ``audit_log`` row; failures are logged only (mirrors invites)."""
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


def invalidate_for_user(ms, user_id: str) -> int:
    """Consume every outstanding token for ``user_id``; return how many died.

    Takes the store explicitly so callers already inside a transaction — most
    importantly :func:`services.users.apply_new_password_in_transaction` —
    invalidate in the same unit of work as the password write. A reset link must
    not outlive the password it was minted to replace.

    **Degrades to a no-op when the table is absent, inside a SAVEPOINT.** Every
    password-change path in the product calls this, including
    ``scripts.reset_password`` — the shell escape hatch a locked-out admin is
    told to use. If migration 073 has not been applied (an interrupted upgrade,
    a manually managed database, a rollback), this must not take the password
    write down with it.

    The savepoint is the whole mechanism, and the first version of this guard
    lacked it. Postgres aborts the **entire** transaction on any statement
    error, so catching ``UndefinedTable`` and returning 0 left the connection in
    ``INERROR``: the next statement raised ``InFailedSqlTransaction`` and
    ``COMMIT`` degraded to ``ROLLBACK``. The guard written to protect
    lockout-recovery therefore broke it, and could report success while the
    password never changed. A bare ``try/except`` cannot make a statement
    optional in Postgres — only ``ROLLBACK TO SAVEPOINT`` can.

    No test caught it because the fakes raise plain Python exceptions, which
    abort nothing. See ``tests/integration/test_password_reset_savepoint_live.py``.
    """
    guard = getattr(ms, "savepoint", None)

    def _run() -> int:
        # SCOPE-EXEMPT: auth credential rows keyed by the subject's own user_id.
        # `visible_filter` models personal-vs-shared household content; a reset
        # token has no scope and is never readable by anyone — matching on
        # user_id here is the ownership check, not a bypass of one.
        rows = ms.fetch_all(
            "UPDATE password_reset_tokens SET used_at = NOW() "
            "WHERE user_id = %s AND used_at IS NULL RETURNING id",
            (user_id,),
        )
        return len(rows or [])

    try:
        if guard is None:
            # No enclosing transaction to protect (a fake, or an autocommit
            # store): there is nothing for a savepoint to save.
            return _run()
        with guard():
            return _run()
    except Exception as exc:  # noqa: BLE001 - narrowed by message below
        if _is_missing_table(exc):
            _log.warning(
                "password_reset_tokens is absent (migration 073 not applied); "
                "skipping reset-token invalidation for this password change"
            )
            return 0
        raise


def _is_missing_table(exc: Exception) -> bool:
    """True for psycopg2's UndefinedTable, without importing psycopg2 here.

    Matched on the SQLSTATE class code (42P01) with a message fallback so the
    service layer stays driver-agnostic and the test fakes can raise a plain
    ``RuntimeError`` carrying the same text.
    """
    if getattr(exc, "pgcode", None) == "42P01":
        return True
    text = str(exc).lower()
    return "undefinedtable" in text or (
        "does not exist" in text and "password_reset_tokens" in text
    )


def build_reset_url(plaintext: str) -> str:
    """Absolute ``{origin}/reset-password?token=…``.

    Raises ``ValueError`` when ``LUMOGIS_PUBLIC_ORIGIN`` is unset. Fail closed
    on purpose: a relative link in an email is useless, and deriving the origin
    from the request's ``Host`` header would let an attacker who can set that
    header redirect a live reset token to a host they control.
    """
    from services.user_invites import build_public_url

    return build_public_url("/reset-password", token=plaintext)


@dataclass(frozen=True)
class MintedToken:
    token_id: str
    plaintext: str
    token_prefix: str
    expires_at: datetime


def _insert_token(ms, token_id, user_id, token_prefix, plaintext, expires_at) -> None:
    ms.execute(
        "INSERT INTO password_reset_tokens "
        "(id, user_id, token_prefix, token_hash, expires_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (token_id, user_id, token_prefix, _hash_plaintext(plaintext), expires_at),
    )


def mint(ms, user_id: str) -> MintedToken:
    """Mint a token for ``user_id``, invalidating that user's older ones.

    Takes the store so the caller can run this inside the same transaction as
    the outbox enqueue — a committed request must never hold a token whose
    mail was not also committed.

    **Each INSERT attempt runs in its own SAVEPOINT**, because the retry only
    works if the failed attempt can be undone. Without one, the unique violation
    aborts the enclosing transaction and the *retried* INSERT fails with
    ``InFailedSqlTransaction`` — a message that does not contain "unique", so it
    is re-raised, `_dispatch_or_pad` swallows it, and the caller is told to check
    an inbox that will never receive anything. A retry loop inside a Postgres
    transaction is decorative unless it rolls back to a savepoint first.
    """
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes())
    invalidate_for_user(ms, user_id)
    guard = getattr(ms, "savepoint", None)

    last_exc: Exception | None = None
    for attempt in range(_MINT_COLLISION_BUDGET):
        plaintext, token_prefix = _generate_plaintext()
        token_id = str(uuid.uuid4())
        try:
            if guard is None:
                _insert_token(ms, token_id, user_id, token_prefix, plaintext, expires_at)
            else:
                with guard():
                    _insert_token(ms, token_id, user_id, token_prefix, plaintext, expires_at)
        except Exception as exc:  # noqa: BLE001 - re-raised unless it is a prefix collision
            last_exc = exc
            if "unique" in str(exc).lower():
                _log.info(
                    "password reset mint prefix collision on attempt %d/%d; regenerating",
                    attempt + 1,
                    _MINT_COLLISION_BUDGET,
                )
                continue
            raise
        return MintedToken(
            token_id=token_id,
            plaintext=plaintext,
            token_prefix=token_prefix,
            expires_at=expires_at,
        )
    raise RuntimeError(
        f"password reset mint collision retry budget exhausted "
        f"({_MINT_COLLISION_BUDGET}); last: {last_exc!r}"
    )


def redeem(plaintext: str, new_password: str) -> str:
    """Consume ``plaintext`` and set the owner's password. Returns the user id.

    Raises :class:`ResetTokenInvalidError` for anything wrong with the token
    and ``services.users.PasswordPolicyViolationError`` for a weak password.
    The consume is a single conditional UPDATE, so two racing redeems of the
    same link cannot both win — the loser sees no row and is told the link is
    invalid, exactly as it would be told for a token that never existed.

    **The consume and the password write are one unit of work.** They were two:
    the UPDATE autocommitted and ``_apply_new_password`` then opened its own
    transaction, so any transient failure in between left the token spent and
    the password unchanged — a retryable error turned into a lockout, on the one
    path whose entire purpose is to end lockouts. The transaction runs on a
    dedicated connection because this is a request path; see
    ``tests/integration/test_postgres_store_concurrency_live.py``.
    """
    from services import users as users_service

    token_prefix = _parse_plaintext(plaintext)
    if token_prefix is None:
        raise ResetTokenInvalidError()

    # Policy first: a weak password must not burn the user's only live link.
    users_service.validate_password_policy(new_password)

    ms = config.get_metadata_store()
    with ms.isolated_transaction() as tx:
        row = tx.fetch_one(
            "UPDATE password_reset_tokens SET used_at = NOW() "
            "WHERE token_prefix = %s AND used_at IS NULL AND expires_at > NOW() "
            "RETURNING id, user_id, token_hash",
            (token_prefix,),
        )
        if row is None:
            raise ResetTokenInvalidError()

        # Constant-time compare against the stored digest. The prefix alone is a
        # lookup key, never proof of possession — a caller who guessed a prefix
        # still cannot redeem.
        #
        # Raising here now rolls the consume back, where it used to commit it.
        # That is the safer direction and it is deliberate: burning the row on a
        # hash mismatch let anyone who could produce a matching prefix destroy
        # the legitimate holder's live link. The prefix is 16 chars drawn from
        # the same 28 random bytes as the token, so reaching this branch at all
        # is already infeasible, and `FailureRateLimiter` bounds the attempts
        # either way.
        if not hmac.compare_digest(_hash_plaintext(plaintext), str(row["token_hash"])):
            _log.warning(
                "password reset: prefix hit with hash mismatch token_prefix=%s "
                "(consume rolled back)",
                token_prefix,
            )
            raise ResetTokenInvalidError()

        user_id = str(row["user_id"])
        rev = users_service.apply_new_password_in_transaction(tx, user_id, new_password)

    # Post-commit only. Cache invalidation and audit must not fire for a change
    # that rolled back — a revoked-session cache purge for a password that never
    # changed would log people out on the strength of a failed write.
    users_service.finish_password_change(rev)
    _emit_audit(
        "__password_reset__.redeemed",
        user_id=user_id,
        input_summary={"token_prefix": token_prefix},
        result_summary={"ok": True},
    )
    return user_id
