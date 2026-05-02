# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Per-user connector credential service.

Single module owning all crypto + table I/O for the
``user_connector_credentials`` table introduced by
``postgres/migrations/015-user-connector-credentials.sql``. See plan
``.cursor/plans/per_user_connector_credentials.plan.md`` and ADR
``.cursor/adrs/per_user_connector_credentials.md`` for the design
contract.

Helper-sharing note (ADR ``credential_scopes_shared_system``)
-------------------------------------------------------------
Crypto, audit, key-fingerprint, actor validation, and env-fallback
helpers now live in :mod:`services._credential_internals` and are
shared with :mod:`services.credential_tiers` (household + instance
system tiers). This module:

* re-exports :class:`CredentialRecord`, :class:`ConnectorNotConfigured`,
  :class:`CredentialUnavailable`, :func:`reset_for_tests`,
  :func:`get_current_key_version` from the internals module, so
  every existing import (``from services.connector_credentials import
  CredentialUnavailable``, etc.) keeps working byte-for-byte;
* owns the action-name vocabulary (:data:`ACTION_CRED_PUT`,
  :data:`ACTION_CRED_DELETED`, :data:`ACTION_CRED_ROTATED`) — the
  tier sibling imports them from here; the import direction is
  one-way (this file never imports ``credential_tiers`` at module
  top, only inside :func:`reencrypt_all_to_current_version` to walk
  the household + system tables);
* keeps every public verb (``get_record``, ``list_records``,
  ``get_payload``, ``put_payload``, ``delete_payload``, ``resolve``,
  ``reencrypt_all_to_current_version``, ``count_rows_by_key_version``,
  ``register_change_listener``, ``reset_listeners_for_tests``)
  exactly as before. Callers do not see the internal split.

Public surface
--------------
* :class:`CredentialRecord` — frozen dataclass mirroring a row's
  metadata (NEVER carries ciphertext or plaintext).
* :func:`get_payload` — decrypt + return the JSON dict, ``None`` on
  miss (registry-strict).
* :func:`get_record` — metadata-only single-row fetch, ``None`` on
  miss (format-strict, NOT registry-strict — admins can read stale
  rows whose connector id has since left the canonical
  :data:`connectors.registry.CONNECTORS` mapping).
* :func:`list_records` — per-user enumeration, no registry filtering.
* :func:`put_payload` — UPSERT + audit + return the fresh
  :class:`CredentialRecord`.
* :func:`delete_payload` — DELETE + audit (format-strict only, so
  stale rows are reachable for cleanup).
* :func:`resolve` — runtime entrypoint with optional env fallback
  (D3); plaintext-bearing, registry-strict.
* :func:`reencrypt_all_to_current_version` — operator rotation
  driven by :meth:`cryptography.fernet.MultiFernet.rotate`. Walks
  per-user, household, and instance-system tables in a single call
  and returns the aggregated ``{rotated, skipped, failed, by_tier}``
  shape.
* :func:`get_current_key_version` — public diagnostic helper.

Registry-strictness model (locked across all six callable verbs)
----------------------------------------------------------------
================ ===================== =========================== ==============================================
Function         ``validate_format``   ``require_registered``      Rationale
================ ===================== =========================== ==============================================
put_payload      yes                   **yes**                     Cannot create rows for unknown connectors.
get_payload      yes                   **yes**                     Plaintext-bearing path; runtime-style use.
resolve          yes                   **yes**                     Runtime path; fail-closed.
get_record       yes                   no                          Operator/admin metadata read on stale rows.
list_records     n/a                   no                          Operator visibility includes historical rows.
delete_payload   yes                   no                          Operator/admin cleanup of stale rows.
================ ===================== =========================== ==============================================
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import config
from connectors import registry as connectors_registry
from connectors.registry import (  # noqa: F401 — re-exported for callers
    UnknownConnector,
)
from services._credential_internals import (  # noqa: F401 — _-prefixed names re-exported for back-compat with pre-refactor test imports
    ConnectorNotConfigured,
    CredentialUnavailable,
    _actor_str,
    _build_multifernet,
    _current_key_version,
    _decrypt_payload,
    _emit_audit as _emit_audit_internal,
    _encrypt_payload,
    _get_multifernet,
    _key_fingerprint,
    _load_keys,
    _PLACEHOLDER_KEYS,
    _resolve_env_fallback,
    get_current_key_version,
    reset_for_tests,
)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audit action_name constants — single owner. The household / instance-system
# tier service (``services/credential_tiers.py``) imports them from here so
# the action-name vocabulary stays unified across all three tables.
# Mirrors the ``__mcp_token__.*`` precedent from ``services/mcp_tokens.py``:
# inline string constants, no central registry.
# ---------------------------------------------------------------------------

ACTION_CRED_PUT = "__connector_credential__.put"
ACTION_CRED_DELETED = "__connector_credential__.deleted"
ACTION_CRED_ROTATED = "__connector_credential__.rotated"


# ---------------------------------------------------------------------------
# CredentialRecord — public dataclass returned by every metadata path.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CredentialRecord:
    """Row metadata as serialised by the route layer.

    Mirrors :class:`models.connector_credential.ConnectorCredentialPublic`
    1:1 so route code can do
    ``ConnectorCredentialPublic.model_validate(record.__dict__)``.

    NEVER carries ciphertext or plaintext.
    """

    user_id: str
    connector: str
    created_at: datetime
    updated_at: datetime
    created_by: str
    updated_by: str
    key_version: int


# ---------------------------------------------------------------------------
# Per-user audit wrapper — pins ``tier="user"`` so every audit row written
# from this module carries the family discriminator. The household + system
# tier service writes its own rows with ``tier="household"`` / ``"system"``.
# ---------------------------------------------------------------------------


def _emit_audit(
    user_id: str,
    connector: str,
    action_name: str,
    *,
    actor: str,
    key_version: int,
    ok: bool = True,
) -> None:
    """Per-user audit wrapper — calls the shared internal with ``tier="user"``.

    Preserves the previous in-module signature exactly so existing tests
    that import this name (and any future test that mocks it) keep
    working without changes. The ``tier="user"`` pin is the no-drift
    contract from plan §Audit-attribution edge cases — per-user
    rotations and writes MUST carry ``user_id == target_user_id`` AND
    ``input_summary["tier"] == "user"``.
    """
    _emit_audit_internal(
        user_id,
        connector,
        action_name,
        actor=actor,
        key_version=key_version,
        tier="user",
        ok=ok,
    )


# ---------------------------------------------------------------------------
# Change listeners — same-process notification for credential mutations.
#
# Pinned by plan ``llm_provider_keys_per_user_migration`` D5.2: when a
# user PUTs/DELETEs an LLM key via ``/api/v1/users/me/credentials/<connector>``,
# the in-memory adapter cache in :mod:`config` must drop that user's
# ``llm:<user_id>:*`` entries so the next chat request rebuilds with the
# fresh key. Listeners run synchronously **after** the DB mutation has
# already been committed by the upsert/delete RETURNING and **after** the
# audit row was emitted, so a slow listener cannot mask write-failure
# vs. listener-failure.
#
# Cross-process limitation (recorded in plan §Operator-visible behaviour
# changes): ``_LISTENERS`` lives in this process. A change made via the
# migration script (a separate Python process) does NOT trigger the
# orchestrator's listeners — operators must restart the orchestrator
# after running ``scripts/migrate_llm_keys_to_per_user.py`` for the new
# keys to take effect. Same caveat applies to multi-replica deployments
# (out of scope for v1 family-LAN).
# ---------------------------------------------------------------------------


_LISTENERS_LOCK = threading.Lock()
_LISTENERS: list[Any] = []  # list of callables


def register_change_listener(listener: Any) -> None:
    """Register a process-local credential-change listener.

    Listener signature::

        listener(*, user_id: str, connector: str, action: str) -> None

    where ``action`` is ``"put"`` or ``"delete"``. Listeners run inline
    on the writing thread immediately after :func:`put_payload` /
    :func:`delete_payload` complete; they MUST be cheap and MUST NOT
    raise (exceptions are caught and logged at exception level so a
    misbehaving listener cannot fail the user-facing write). Order of
    registration is preserved.
    """
    with _LISTENERS_LOCK:
        _LISTENERS.append(listener)


def reset_listeners_for_tests() -> None:
    """Drop all registered listeners. Tests-only sibling of :func:`reset_for_tests`."""
    with _LISTENERS_LOCK:
        _LISTENERS.clear()


def _fire_change(user_id: str, connector: str, *, action: str) -> None:
    """Invoke every registered listener; never raises."""
    with _LISTENERS_LOCK:
        listeners = list(_LISTENERS)
    for listener in listeners:
        try:
            listener(user_id=user_id, connector=connector, action=action)
        except Exception:
            _log.exception(
                "connector_credentials change listener %r raised "
                "(user_id=%s connector=%s action=%s); ignoring",
                listener, user_id, connector, action,
            )


# ---------------------------------------------------------------------------
# Per-user diagnostic counter (used by the household + system tier sibling
# functions in identical shape).
# ---------------------------------------------------------------------------


def count_rows_by_key_version() -> dict[int, int]:
    """Group ``user_connector_credentials`` rows by ``key_version``.

    Returns ``{}`` on an empty table. Includes rows whose ``connector``
    id is not (or is no longer) in the canonical
    :data:`connectors.registry.CONNECTORS` mapping — registry-strictness
    deliberately does not gate diagnostics (operators need to see every
    sealed row, including stale ones, when planning a key rotation).

    Used by ``GET /api/v1/admin/diagnostics/credential-key-fingerprint``
    (plan ``credential_management_ux`` D3 + D3a, generalised by ADR
    ``credential_scopes_shared_system`` to a per-tier breakdown).
    Never logs ciphertext or plaintext — counts only.
    """
    ms = config.get_metadata_store()
    rows = ms.fetch_all(
        "SELECT key_version, COUNT(*) AS n "
        "FROM user_connector_credentials "
        "GROUP BY key_version "
        "ORDER BY key_version"
    )
    return {int(r["key_version"]): int(r["n"]) for r in rows}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record_from_row(row: dict) -> CredentialRecord:
    """Build a :class:`CredentialRecord` from a SELECT result dict."""
    return CredentialRecord(
        user_id=row["user_id"],
        connector=row["connector"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        created_by=row["created_by"],
        updated_by=row["updated_by"],
        key_version=row["key_version"],
    )


# ---------------------------------------------------------------------------
# Public API — metadata reads (format-strict, NOT registry-strict)
# ---------------------------------------------------------------------------


_SELECT_RECORD_COLS = (
    "user_id, connector, created_at, updated_at, "
    "created_by, updated_by, key_version"
)


def get_record(user_id: str, connector: str) -> CredentialRecord | None:
    """Return row metadata, ``None`` on miss.

    Format-strict, NOT registry-strict: admins / operators can read
    metadata of historical-but-still-stored rows whose connector id
    has since been removed from the canonical
    :data:`connectors.registry.CONNECTORS` mapping.
    Never decrypts. Never raises :class:`CredentialUnavailable` —
    corrupt rows still return a valid :class:`CredentialRecord`
    because metadata reads do not touch ``ciphertext``.
    """
    connectors_registry.validate_format(connector)
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: user_connector_credentials has no `scope` column
    # — these are encrypted per-user external-service secrets, not
    # authored memory; the (user_id, connector) primary key already
    # makes the row uniquely owned by exactly one user.
    row = ms.fetch_one(
        f"SELECT {_SELECT_RECORD_COLS} FROM user_connector_credentials "
        "WHERE user_id = %s AND connector = %s",
        (user_id, connector),
    )
    return _record_from_row(row) if row else None


def list_records(user_id: str) -> list[CredentialRecord]:
    """Per-user enumeration ordered by ``connector`` ASC.

    No registry filtering — returns whatever the table holds for the
    user, including historical-but-still-stored connectors that have
    since been removed from the canonical
    :data:`connectors.registry.CONNECTORS` mapping. This is
    deliberate (operator visibility / cleanup).
    """
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: user_connector_credentials has no `scope` column
    # — credentials table, not memory; rows are owned by exactly one
    # user via the (user_id, connector) primary key.
    rows = ms.fetch_all(
        f"SELECT {_SELECT_RECORD_COLS} FROM user_connector_credentials "
        "WHERE user_id = %s ORDER BY connector ASC",
        (user_id,),
    )
    return [_record_from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Public API — plaintext reads (registry-strict)
# ---------------------------------------------------------------------------


def get_payload(user_id: str, connector: str) -> dict[str, Any] | None:
    """Decrypt + return the JSON payload, ``None`` on miss.

    Plaintext-bearing path; **registry-strict** by design (calls
    :func:`connectors.registry.require_registered`). Operators reading
    metadata for a stale row use :func:`get_record` instead, which
    does NOT require registration.
    """
    connectors_registry.require_registered(connector)
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: user_connector_credentials has no `scope` column
    # — encrypted per-user external-service secrets, not authored memory.
    row = ms.fetch_one(
        "SELECT ciphertext FROM user_connector_credentials "
        "WHERE user_id = %s AND connector = %s",
        (user_id, connector),
    )
    if row is None:
        return None
    return _decrypt_payload(row["ciphertext"])


# ---------------------------------------------------------------------------
# Public API — writes (registry-strict)
# ---------------------------------------------------------------------------


def put_payload(
    user_id: str,
    connector: str,
    payload: dict[str, Any],
    *,
    actor: str,
) -> CredentialRecord:
    """UPSERT a credential row; emit audit; return the fresh record.

    Registry-strict (calls
    :func:`connectors.registry.require_registered` — unknown
    connectors raise :class:`UnknownConnector` which the route maps
    to HTTP 422). ``created_by`` / ``created_at`` stay first-write;
    ``updated_by`` / ``updated_at`` advance on every call.
    """
    connectors_registry.require_registered(connector)
    actor_clean = _actor_str(actor)
    ciphertext = _encrypt_payload(payload)
    key_version = _current_key_version()

    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "INSERT INTO user_connector_credentials "
        "(user_id, connector, ciphertext, key_version, created_by, updated_by) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (user_id, connector) DO UPDATE SET "
        "  ciphertext = EXCLUDED.ciphertext, "
        "  key_version = EXCLUDED.key_version, "
        "  updated_at = NOW(), "
        "  updated_by = EXCLUDED.updated_by "
        f"RETURNING {_SELECT_RECORD_COLS}",
        (
            user_id,
            connector,
            ciphertext,
            key_version,
            actor_clean,
            actor_clean,
        ),
    )
    if row is None:
        raise RuntimeError(
            f"connector_credentials put: row not visible after upsert "
            f"user_id={user_id!r} connector={connector!r}"
        )
    record = _record_from_row(row)
    _emit_audit(
        user_id,
        connector,
        ACTION_CRED_PUT,
        actor=actor_clean,
        key_version=key_version,
    )
    _log.info(
        "connector_credentials: put user_id=%s connector=%s actor=%s",
        user_id, connector, actor_clean,
    )
    _fire_change(user_id, connector, action="put")
    return record


# ---------------------------------------------------------------------------
# Public API — delete (format-strict only)
# ---------------------------------------------------------------------------


def delete_payload(
    user_id: str,
    connector: str,
    *,
    actor: str,
) -> bool:
    """Delete a row; emit audit on hit; return True iff a row was deleted.

    Format-strict only — admins/operators can clean up rows whose
    connector id has since been removed from the canonical
    :data:`connectors.registry.CONNECTORS` mapping. The audit record
    preserves a paper trail that the historical connector id existed
    and was cleaned up.
    """
    connectors_registry.validate_format(connector)
    actor_clean = _actor_str(actor)
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: user_connector_credentials has no `scope` column
    # — encrypted per-user external-service secrets, not authored memory.
    row = ms.fetch_one(
        "DELETE FROM user_connector_credentials "
        "WHERE user_id = %s AND connector = %s "
        "RETURNING key_version",
        (user_id, connector),
    )
    if row is None:
        return False
    _emit_audit(
        user_id,
        connector,
        ACTION_CRED_DELETED,
        actor=actor_clean,
        key_version=row["key_version"],
    )
    _log.info(
        "connector_credentials: deleted user_id=%s connector=%s actor=%s",
        user_id, connector, actor_clean,
    )
    _fire_change(user_id, connector, action="delete")
    return True


# ---------------------------------------------------------------------------
# Public API — runtime resolve (registry-strict, env-fallback aware)
# ---------------------------------------------------------------------------


def resolve(
    user_id: str,
    connector: str,
    *,
    fallback_env: str | None = None,
) -> dict[str, Any]:
    """Runtime entrypoint for downstream consumers (D3).

    Registry-strict. The env-fallback logic is delegated to
    :func:`services._credential_internals._resolve_env_fallback` so the
    runtime tier resolver and this per-user resolver share a single
    canonical implementation (no byte-for-byte mirror to drift).
    """
    connectors_registry.require_registered(connector)

    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: user_connector_credentials has no `scope` column
    # — encrypted per-user external-service secrets, not authored memory.
    row = ms.fetch_one(
        "SELECT ciphertext FROM user_connector_credentials "
        "WHERE user_id = %s AND connector = %s",
        (user_id, connector),
    )
    if row is not None:
        return _decrypt_payload(row["ciphertext"])

    env_payload = _resolve_env_fallback(connector, fallback_env)
    if env_payload is not None:
        return env_payload

    raise ConnectorNotConfigured(
        f"no credential row and no usable env fallback for "
        f"user_id={user_id!r} connector={connector!r}"
    )


# ---------------------------------------------------------------------------
# Operator rotation
# ---------------------------------------------------------------------------


_ALL_CRED_TABLES: tuple[str, ...] = (
    "user_connector_credentials",
    "household_connector_credentials",
    "instance_system_connector_credentials",
)


def _reencrypt_user_table(*, actor_clean: str) -> dict[str, int]:
    """Walk + re-seal the per-user table. Returns per-tier counters."""
    mf = _get_multifernet()
    current_fp = _current_key_version()

    ms = config.get_metadata_store()
    rows = ms.fetch_all(
        "SELECT user_id, connector, ciphertext, key_version "
        "FROM user_connector_credentials "
        "ORDER BY user_id ASC, connector ASC"
    )

    rotated = 0
    skipped = 0
    failed = 0

    for row in rows:
        user_id = row["user_id"]
        connector = row["connector"]
        old_ciphertext = bytes(row["ciphertext"])

        if row["key_version"] == current_fp:
            skipped += 1
            continue

        try:
            new_token = mf.rotate(old_ciphertext)
        except Exception as exc:
            _log.error(
                "rotate failed for user_id=%s connector=%s "
                "(%s); ciphertext bytes NOT logged",
                user_id, connector, exc.__class__.__name__,
            )
            failed += 1
            continue

        try:
            mf.decrypt(new_token)
        except Exception as exc:
            _log.error(
                "rotate verify failed for user_id=%s connector=%s "
                "(%s); ciphertext bytes NOT logged",
                user_id, connector, exc.__class__.__name__,
            )
            failed += 1
            continue

        try:
            # SCOPE-EXEMPT: credentials table, no `scope` column;
            # rotation walks every row owned by every user.
            ms.execute(
                "UPDATE user_connector_credentials "
                "SET ciphertext = %s, key_version = %s, "
                "    updated_at = NOW(), updated_by = %s "
                "WHERE user_id = %s AND connector = %s",
                (new_token, current_fp, actor_clean, user_id, connector),
            )
        except Exception as exc:
            _log.error(
                "rotate UPDATE failed for user_id=%s connector=%s (%s)",
                user_id, connector, exc.__class__.__name__,
            )
            failed += 1
            continue

        # Per-user rotation audit MUST carry the row's owner user_id —
        # not the rotation actor (`"system"` for the script run; an
        # admin's id for an admin-driven retry). This preserves owner
        # identity in the per-user audit trail and is a different
        # contract from household/system rotation, which uses the
        # acting admin's id (or `"default"` for `actor="system"`).
        _emit_audit(
            user_id,
            connector,
            ACTION_CRED_ROTATED,
            actor=actor_clean,
            key_version=current_fp,
        )
        rotated += 1

    return {"rotated": rotated, "skipped": skipped, "failed": failed}


def reencrypt_all_to_current_version(
    *,
    actor: str = "system",
    tables: tuple[str, ...] = _ALL_CRED_TABLES,
) -> dict[str, Any]:
    """Re-encrypt rows in the requested tables to the current primary key.

    The per-user table is walked in-module (legacy code path, preserved
    byte-for-byte). The household and instance-system tier tables are
    delegated to :func:`services.credential_tiers.reencrypt_household_to_current_version`
    and :func:`services.credential_tiers.reencrypt_system_to_current_version`
    so the SQL for those tables stays in their owning module.

    Skip predicate is ``row.key_version == current_fp`` — the stable
    32-bit unsigned fingerprint of the current primary key. Rotation
    uses :meth:`cryptography.fernet.MultiFernet.rotate`, which always
    emits a fresh IV, so ``key_version`` is the only sound "already
    sealed by the current primary?" signal.

    Returns the aggregated shape::

        {
            "rotated": int,    # sum across all walked tiers
            "skipped": int,    # sum
            "failed":  int,    # sum
            "by_tier": {
                "user":      {"rotated": int, "skipped": int, "failed": int},
                "household": {...},   # only present if walked
                "system":    {...},   # only present if walked
            },
        }

    Per-tier work is independent — a failure in the household walk
    never rolls back per-user updates, and vice versa.
    """
    # IMPORTANT: lazy import to avoid circular dependency at boot.
    # services.credential_tiers imports ACTION_CRED_* from THIS module
    # at top level (the action-name vocabulary). Importing cts at
    # module-top here would create a circular ImportError on first
    # import of either module. Pinned by ADR
    # credential_scopes_shared_system §Modified files / rotation
    # function (D2.3 CRITICAL fix).
    from services import credential_tiers as cts

    actor_clean = _emit_audit_actor_passthrough(actor)

    by_tier: dict[str, dict[str, int]] = {}

    if "user_connector_credentials" in tables:
        by_tier["user"] = _reencrypt_user_table(actor_clean=actor_clean)

    if "household_connector_credentials" in tables:
        by_tier["household"] = cts.reencrypt_household_to_current_version(
            actor=actor_clean,
        )

    if "instance_system_connector_credentials" in tables:
        by_tier["system"] = cts.reencrypt_system_to_current_version(
            actor=actor_clean,
        )

    totals = {"rotated": 0, "skipped": 0, "failed": 0}
    for sub in by_tier.values():
        for k in totals:
            totals[k] += int(sub.get(k, 0))

    _log.info(
        "connector_credentials rotation summary: rotated=%d skipped=%d failed=%d "
        "by_tier=%s",
        totals["rotated"], totals["skipped"], totals["failed"], by_tier,
    )
    return {
        "rotated": totals["rotated"],
        "skipped": totals["skipped"],
        "failed": totals["failed"],
        "by_tier": by_tier,
    }


def _emit_audit_actor_passthrough(actor: str) -> str:
    """Validate ``actor`` once for the rotation entrypoint.

    ``_actor_str`` (per-user) accepts ``self|system|admin:<id>``. The
    rotation entrypoint is invoked by the operator script (default
    ``"system"``) or by a future admin-triggered retry path
    (``"admin:<id>"``). ``"self"`` is technically accepted but never
    expected here — the per-user rotation walk does not own a single
    user identity, so passing ``"self"`` would attribute rotation
    audit rows to the literal string ``"self"`` instead of a real
    actor. Documented as a non-error to keep symmetry with
    :func:`put_payload` / :func:`delete_payload`.
    """
    return _actor_str(actor)


# ---------------------------------------------------------------------------
# Public surface freeze (test_connector_credentials_public_surface.py).
# ---------------------------------------------------------------------------

__all__ = [
    "ACTION_CRED_PUT",
    "ACTION_CRED_DELETED",
    "ACTION_CRED_ROTATED",
    "CredentialRecord",
    "ConnectorNotConfigured",
    "CredentialUnavailable",
    "UnknownConnector",
    "get_record",
    "list_records",
    "get_payload",
    "put_payload",
    "delete_payload",
    "resolve",
    "count_rows_by_key_version",
    "reencrypt_all_to_current_version",
    "get_current_key_version",
    "reset_for_tests",
    "register_change_listener",
    "reset_listeners_for_tests",
]
