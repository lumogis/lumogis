# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""MCP token service — owns the lifecycle of `mcp_tokens` rows.

Single responsibility: mint / verify / list / revoke / cascade-revoke /
throttled `last_used_at` stamping. Pure service layer — NO HTTP, NO
request state. Routes (in `routes/mcp_tokens.py`) and the auth gate
(in `auth.py::_check_mcp_bearer`) are the only callers.

See plan ``mcp_token_user_map`` (D2/D3/D4/D5/D7/D9/D14) and ADR
``mcp_token_user_map.md`` for the design contract. This module is
the implementation half.

Token shape (D2)
----------------
  ``lmcp_<base32(28 random bytes, lowercase, no padding)>``

  * 28 bytes of CSPRNG output → 45 base32 chars → 50-char total.
  * `token_prefix` = first 16 chars of the body (~80 bits of entropy).
  * `token_hash` = SHA-256 hex of the FULL plaintext (D9).

Verification path (called on every `/mcp/*` request)
----------------------------------------------------
  startswith("lmcp_") → extract 16-char prefix → indexed lookup on
  (token_prefix, revoked_at IS NULL) → hmac.compare_digest the SHA-256
  of the presented token against `row.token_hash` → return the row OR
  None. On hit, schedule a throttled `last_used_at` stamp via D5's
  `_LAST_STAMP_CACHE`. Target: < 1 ms p95.

Audit emission (D14)
--------------------
  All four `__mcp_token__.*` action_name strings are written via
  ``actions/audit.py::write_audit(AuditEntry(...))`` to ``audit_log``
  — NOT ``permissions.log_action`` (which writes to ``action_log``,
  a different table for connector tool-permission checks). The
  per-action field shape mirrors ``services/user_export.py:330-364``
  exactly. Audit failures are caught and logged at exception level,
  never re-raised — a missed audit row is a hygiene loss; missing the
  underlying mutation would be a contract violation.

Cascade contract (D7, hardened in arbitration R1)
--------------------------------------------------
  Called by ``services/users.set_disabled(disabled=True)`` when the
  admin route flips a user. ``cascade_revoke_for_user`` revokes every
  active token for the target user_id in a single SQL UPDATE and
  returns the affected rows so the caller can emit one
  ``__mcp_token__.cascade_revoked`` audit row per token, attributing
  to the ADMIN who flipped the user (NOT the disabled user). The
  caller decides whether to wrap the cascade + disable flip in a
  transaction; this function does not open one itself.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import threading
import uuid
from collections import OrderedDict
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Optional

from actions.audit import write_audit
from models.actions import AuditEntry
from models.mcp_token import InternalMcpToken

import config

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audit action_name constants (D14).
# Mirrors the `__user_export__.*` precedent from services/user_export.py:
# inline string constants are the convention; there is no central registry.
# ---------------------------------------------------------------------------

ACTION_MINTED = "__mcp_token__.minted"
ACTION_REVOKED = "__mcp_token__.revoked"
ACTION_ADMIN_REVOKED = "__mcp_token__.admin_revoked"
ACTION_CASCADE_REVOKED = "__mcp_token__.cascade_revoked"


# ---------------------------------------------------------------------------
# Token-format constants (D2).
# ---------------------------------------------------------------------------

_TOKEN_PREFIX_TAG = "lmcp_"
_TOKEN_BODY_BYTES = 28  # 28 bytes CSPRNG → 45 base32 chars
_TOKEN_BODY_LEN = 45  # base32(28 bytes) without padding
_LOOKUP_PREFIX_LEN = 16  # D2: first 16 chars of the body
_TOTAL_TOKEN_LEN = len(_TOKEN_PREFIX_TAG) + _TOKEN_BODY_LEN  # 5 + 45 = 50

# Bounded retry budget on (astronomically improbable) prefix collisions.
# 80 bits of entropy in the 16-char prefix means the chance of hitting an
# active duplicate is ≈ N/2^80; with N=hundreds of thousands of tokens it
# remains negligible. The budget exists so a misconfigured CSPRNG
# (e.g. a test that monkey-patches `secrets.token_bytes` to a constant)
# fails loud rather than spinning forever.
_MINT_COLLISION_BUDGET = 5


# ---------------------------------------------------------------------------
# `last_used_at` write throttle (D5).
#
# verify() is on the hot `/mcp/*` path. We do not need (and do not want)
# to issue an UPDATE for every successful verify — the column is hygiene
# metadata, not a counter. We cache the last-recorded `last_used_at` per
# token_id and only schedule a fresh write when the cache is empty or
# older than 5 minutes.
#
# A simple LRU bounded at 4096 entries — well above any realistic
# family-LAN token count — keeps the cache from growing without bound
# in the unlikely event of a long-running process that has seen many
# minted tokens. On process restart the cache is empty and the first
# verify of every token writes once; subsequent verifies are throttled
# again. The 5-minute window is hygiene, not security: a missed stamp
# is a stale `last_used_at`, never a security risk.
# ---------------------------------------------------------------------------

_LAST_STAMP_INTERVAL = timedelta(minutes=5)
_LAST_STAMP_CACHE_MAX = 4096


class _LRUCache:
    """Tiny thread-safe LRU. Bounded by `maxsize`; oldest evicted on overflow.

    Kept module-private — we don't want to grow a generic utility surface
    just for this single, well-bounded use case.
    """

    def __init__(self, maxsize: int) -> None:
        self._maxsize = maxsize
        self._data: "OrderedDict[str, datetime]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[datetime]:
        with self._lock:
            val = self._data.get(key)
            if val is not None:
                self._data.move_to_end(key)
            return val

    def put(self, key: str, value: datetime) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_LAST_STAMP_CACHE = _LRUCache(maxsize=_LAST_STAMP_CACHE_MAX)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_internal(row: dict) -> InternalMcpToken:
    """Adapt a Postgres row dict to an :class:`InternalMcpToken`."""
    return InternalMcpToken(
        id=row["id"],
        user_id=row["user_id"],
        token_prefix=row["token_prefix"],
        token_hash=row["token_hash"],
        label=row["label"],
        scopes=row.get("scopes"),
        created_at=row["created_at"],
        last_used_at=row.get("last_used_at"),
        expires_at=row.get("expires_at"),
        revoked_at=row.get("revoked_at"),
    )


def _generate_plaintext() -> tuple[str, str]:
    """Mint one fresh plaintext token. Returns ``(plaintext, token_prefix)``.

    plaintext shape per D2: ``lmcp_<45 base32 lowercase chars, no padding>``.
    """
    raw = secrets.token_bytes(_TOKEN_BODY_BYTES)
    body = base64.b32encode(raw).decode("ascii").rstrip("=").lower()
    plaintext = _TOKEN_PREFIX_TAG + body
    return plaintext, body[:_LOOKUP_PREFIX_LEN]


def _hash_plaintext(plaintext: str) -> str:
    """Return SHA-256 hex of ``plaintext`` (D9)."""
    return hashlib.sha256(plaintext.encode("ascii")).hexdigest()


def _emit_audit(
    action: str,
    *,
    user_id: str,
    input_summary: dict | None = None,
    result_summary: dict | None = None,
) -> None:
    """Write a single ``audit_log`` row per D14.

    Mirrors ``services/user_export.py::_audit_event`` exactly: ``connector``
    is the routing-ownership tag (``"auth"`` for this surface), ``mode`` is
    ``"system"`` (these are operator events, not connector ASK/DO calls),
    summaries are JSON strings, and audit-write failures are caught and
    logged but NEVER re-raised — a missed audit row is a hygiene loss; a
    failed mint/revoke would be a contract violation. The route layer
    keeps its 200/201 commitment regardless.
    """
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def mint(user_id: str, label: str) -> tuple[InternalMcpToken, str]:
    """Mint a fresh `lmcp_…` token for ``user_id``. Returns the row + plaintext.

    Parameters
    ----------
    user_id:
        The owner. Caller is responsible for ensuring the id corresponds
        to a real ``users`` row — the route layer does this via the
        authenticated session; admin mint-on-behalf is intentionally NOT
        exposed (per D12).
    label:
        Human-readable name shown in the dashboard list. 1..64 chars
        post-strip; the route enforces validation, the service trusts.

    Returns
    -------
    ``(InternalMcpToken, plaintext)``: the freshly inserted row, plus the
    plaintext bearer that is RETURNED EXACTLY ONCE to the operator. The
    server only persists ``token_hash`` (SHA-256) — once the response is
    out of the client's hands, the plaintext is unrecoverable.

    Notes
    -----
    The INSERT statement explicitly passes ``scopes = NULL`` (D3): the
    migration deliberately does NOT declare a column DEFAULT precisely so
    an accidental implementation that omits the column lands ``NULL``
    (the v1 "unrestricted" semantic), never ``[]`` (which D3 reserves as
    "no access"). Pinned by ``test_mint_inserts_scopes_as_null_not_empty_array``.

    On the (astronomically unlikely) event that the freshly minted prefix
    collides with another active row's prefix, regenerate up to
    ``_MINT_COLLISION_BUDGET`` times. The partial unique index
    ``mcp_tokens_active_prefix_uniq`` is the source of truth — Postgres
    raises ``UniqueViolation`` on the INSERT and we catch and retry. If
    every retry collides, raise ``RuntimeError`` (a misconfigured CSPRNG
    is the only realistic culprit; spinning forever would mask it).
    """
    ms = config.get_metadata_store()
    last_exc: Exception | None = None
    for attempt in range(_MINT_COLLISION_BUDGET):
        plaintext, token_prefix = _generate_plaintext()
        token_hash = _hash_plaintext(plaintext)
        token_id = uuid.uuid4().hex
        try:
            ms.execute(
                "INSERT INTO mcp_tokens "
                "(id, user_id, token_prefix, token_hash, label, scopes) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (token_id, user_id, token_prefix, token_hash, label, None),
            )
        except Exception as exc:
            # psycopg surfaces unique-violation as IntegrityError. We don't
            # type-check the exception class — we check the message + retry
            # budget. Any other failure (Postgres down, etc.) bubbles after
            # the budget is exhausted.
            last_exc = exc
            msg = str(exc).lower()
            if "mcp_tokens_active_prefix_uniq" in msg or "unique" in msg:
                _log.info(
                    "mcp_token mint prefix collision on attempt %d/%d; regenerating",
                    attempt + 1,
                    _MINT_COLLISION_BUDGET,
                )
                continue
            raise
        row = ms.fetch_one("SELECT * FROM mcp_tokens WHERE id = %s", (token_id,))
        if row is None:
            raise RuntimeError(f"mcp_token mint: row not visible after insert id={token_id}")
        return _row_to_internal(row), plaintext

    raise RuntimeError(
        "mcp_token mint collision retry budget exhausted "
        f"({_MINT_COLLISION_BUDGET} attempts); last error: {last_exc!r}"
    )


def verify(presented: str) -> InternalMcpToken | None:
    """Verify a presented bearer; return the row on hit, ``None`` on miss.

    The hot path on every `/mcp/*` request. Pure-function shape: lookup
    by indexed prefix, constant-time hash compare, throttled
    ``last_used_at`` stamp on hit. Never raises on bad input — invalid
    bearers just return ``None`` and the gate maps that to 401.

    Resolution rule:
        ``startswith("lmcp_")``  → no, return None
        body length sanity        → too short, return None
        prefix lookup             → no row, return None
        SHA-256 + compare_digest  → mismatch, log WARNING (without the
                                    bearer) and return None
        hit                       → schedule throttled stamp, return row
    """
    if not isinstance(presented, str) or not presented.startswith(_TOKEN_PREFIX_TAG):
        return None
    body = presented[len(_TOKEN_PREFIX_TAG) :]
    if len(body) < _LOOKUP_PREFIX_LEN:
        return None
    token_prefix = body[:_LOOKUP_PREFIX_LEN]

    ms = config.get_metadata_store()
    try:
        row = ms.fetch_one(
            "SELECT * FROM mcp_tokens WHERE token_prefix = %s AND revoked_at IS NULL",
            (token_prefix,),
        )
    except Exception:
        # Storage error is a security-relevant failure: returning None
        # makes the gate 401 (closed). We log loudly so an operator sees
        # the underlying Postgres outage rather than a flood of "invalid
        # mcp token" lines with no context.
        _log.exception("mcp_token verify: storage lookup failed")
        return None
    if row is None:
        return None

    presented_hash = _hash_plaintext(presented)
    if not hmac.compare_digest(presented_hash, row["token_hash"]):
        # Known-prefix + wrong-hash is a meaningful operator signal: it
        # means a token's first 16 chars were probed. We log with the
        # token id (NOT the bearer) so the operator can correlate without
        # the secret material reaching the log.
        _log.warning(
            "mcp_token prefix hit with hash mismatch — possible token leak / typo; token_id=%s",
            row["id"],
        )
        return None

    token = _row_to_internal(row)
    _maybe_stamp_used(token.id)
    return token


def list_for_user(
    user_id: str,
    *,
    include_revoked: bool = False,
) -> list[InternalMcpToken]:
    """Return every token for ``user_id``, newest-created first.

    ``include_revoked=False`` (default) hides revoked rows — the dashboard
    "Active tokens" table. ``include_revoked=True`` returns everything for
    the "Revoked tokens" collapsible section. Both cases are user-scoped
    by ``user_id``; the route layer is the gate that authorises the call.
    """
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: mcp_tokens has no `scope` column — these are credentials,
    # not memory rows. Per-user filtering is the entire authorization
    # contract for the table; visible_filter() does not apply.
    if include_revoked:
        rows = ms.fetch_all(
            "SELECT * FROM mcp_tokens WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,),
        )
    else:
        # SCOPE-EXEMPT: see preceding block — credentials table, not memory.
        rows = ms.fetch_all(
            "SELECT * FROM mcp_tokens "
            "WHERE user_id = %s AND revoked_at IS NULL "
            "ORDER BY created_at DESC",
            (user_id,),
        )
    return [_row_to_internal(r) for r in rows]


def list_all(*, include_revoked: bool = False) -> list[InternalMcpToken]:
    """Global enumeration of every token (NOT routed in v1).

    Reserved for two specific callers per the plan:
      (a) future audit/forensic tooling that wants a "list every active
          token across the household" view; and
      (b) the unit test ``test_list_all_excludes_revoked_by_default`` that
          exercises the ``include_revoked`` toggle alongside
          :func:`list_for_user`.

    Implementer guidance: do NOT register a route for this in v1. Admin
    enumeration is per-user via ``GET /api/v1/admin/users/{user_id}/mcp-tokens``
    (D12). This function is intentional dead code on the routing surface.
    """
    ms = config.get_metadata_store()
    if include_revoked:
        rows = ms.fetch_all("SELECT * FROM mcp_tokens ORDER BY created_at DESC")
    else:
        rows = ms.fetch_all(
            "SELECT * FROM mcp_tokens WHERE revoked_at IS NULL ORDER BY created_at DESC"
        )
    return [_row_to_internal(r) for r in rows]


def get_by_id(token_id: str) -> InternalMcpToken | None:
    """Single-row lookup by primary key.

    Used by the route layer for ownership checks on the DELETE flows
    (the user-facing route returns 404 — not 403 — when ``row.user_id``
    doesn't match the caller, per the info-leak guard in the plan).

    Does NOT filter by ``revoked_at``: re-revoking is allowed and
    idempotent, so the route handler needs to see revoked rows too.
    """
    ms = config.get_metadata_store()
    row = ms.fetch_one("SELECT * FROM mcp_tokens WHERE id = %s", (token_id,))
    return _row_to_internal(row) if row else None


def revoke(
    token_id: str,
    *,
    by_user_id: str,
    by_role: str,
) -> InternalMcpToken | None:
    """Mark ``token_id`` as revoked and return the resulting row.

    Idempotent: revoking an already-revoked row leaves ``revoked_at``
    unchanged (the UPDATE only fires on the active partial). Returns
    ``None`` only if the row does not exist at all.

    The ``by_user_id`` / ``by_role`` parameters are not used by this
    function directly — the service trusts its caller for authorisation.
    They are part of the signature so the route layer can pass them
    straight into the audit emission alongside the row, and so future
    audit columns (``mcp_token_id`` on ``audit_log``) have a stable
    point to flow through.
    """
    del by_user_id, by_role  # consumed by the route's audit emission
    ms = config.get_metadata_store()
    ms.execute(
        "UPDATE mcp_tokens SET revoked_at = NOW() WHERE id = %s AND revoked_at IS NULL",
        (token_id,),
    )
    row = ms.fetch_one("SELECT * FROM mcp_tokens WHERE id = %s", (token_id,))
    if row is None:
        return None
    # Drop any cached `last_used_at` entry — the row is dead to verify().
    _LAST_STAMP_CACHE.put(token_id, datetime.now(timezone.utc))
    return _row_to_internal(row)


def cascade_revoke_for_user(
    user_id: str,
    *,
    by_admin_user_id: str,
) -> list[InternalMcpToken]:
    """Revoke every active token for ``user_id``; return the affected rows.

    Called by ``services/users.set_disabled(disabled=True)`` per D7. The
    caller emits one ``__mcp_token__.cascade_revoked`` audit row per
    returned token, attributing each row to the **admin** who flipped the
    user (NOT to the disabled user themselves).

    Atomicity is the caller's responsibility — see ``set_disabled`` for
    the with-transaction / without-transaction split. This function
    issues a single SQL statement; either it succeeds and returns the
    updated rows, or it raises and the caller decides what to do.

    The ``by_admin_user_id`` parameter is not used by this function
    directly (audit emission lives in the caller, where the row context
    is already available). It is required in the signature to make the
    "who flipped this user" attribution greppable from a single place.
    """
    del by_admin_user_id  # consumed by the caller's audit emission
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: mcp_tokens has no `scope` column — credentials table,
    # not memory. Cascade revocation by user_id IS the contract (D7).
    rows = ms.fetch_all(
        "UPDATE mcp_tokens SET revoked_at = NOW() "
        "WHERE user_id = %s AND revoked_at IS NULL "
        "RETURNING *",
        (user_id,),
    )
    if rows:
        # Stamp every revoked id so a racing verify() sees the freshest
        # `last_used_at` cached value. Cheap; bounded by the per-user
        # token count (realistically tens).
        now = datetime.now(timezone.utc)
        for r in rows:
            _LAST_STAMP_CACHE.put(r["id"], now)
    return [_row_to_internal(r) for r in rows]


def stamp_used(token_id: str) -> None:
    """Internal: bump ``last_used_at = NOW()`` for ``token_id``.

    Best-effort: a failure here is a hygiene loss (a stale
    ``last_used_at`` column for one token), not a security loss. Logs at
    WARNING and never propagates. Called from :func:`verify` only when
    :func:`_maybe_stamp_used` decides the in-process throttle window
    has elapsed.
    """
    ms = config.get_metadata_store()
    try:
        ms.execute(
            "UPDATE mcp_tokens SET last_used_at = NOW() WHERE id = %s",
            (token_id,),
        )
    except Exception as exc:
        _log.warning("mcp_token stamp_used failed for id=%s: %s", token_id, exc)


def _maybe_stamp_used(token_id: str) -> None:
    """Apply the D5 5-minute write throttle around :func:`stamp_used`.

    Consults ``_LAST_STAMP_CACHE``: if the cache has no entry for
    ``token_id`` OR the cached value is older than ``_LAST_STAMP_INTERVAL``,
    schedule a fresh write and update the cache. Otherwise, skip — the DB
    column is fresh enough.
    """
    now = datetime.now(timezone.utc)
    last = _LAST_STAMP_CACHE.get(token_id)
    if last is not None and (now - last) < _LAST_STAMP_INTERVAL:
        return
    stamp_used(token_id)
    # Update the cache to the optimistic "now" — even if the UPDATE
    # silently failed (logged), retrying within the next 5 minutes is
    # not productive (the same DB error will keep firing). The next
    # window will retry organically.
    _LAST_STAMP_CACHE.put(token_id, now)
