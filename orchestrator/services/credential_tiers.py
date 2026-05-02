# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Household + instance/system connector credential tier service.

Sibling to :mod:`services.connector_credentials` (per-user tier).
Owns CRUD on the two new credential tier tables introduced by
``postgres/migrations/018-household-and-instance-system-connector-credentials.sql``
plus the runtime tier resolver
:func:`resolve_runtime_credential`.

See ADR ``credential_scopes_shared_system.md`` and plan
``.cursor/plans/credential_scopes_shared_system.plan.md`` for the
design contract.

Public surface
--------------
* :class:`ResolvedCredential` — frozen dataclass returned by
  :func:`resolve_runtime_credential`. ``__repr__`` is overridden to
  redact the plaintext payload (defence-in-depth against accidental
  debug logging of secrets).
* :func:`resolve_runtime_credential` — the runtime entrypoint: walks
  ``user → household → system → env`` and returns a
  :class:`ResolvedCredential`. Decrypt failure on **any** tier
  immediately raises :class:`services.connector_credentials.CredentialUnavailable`
  — does NOT fall through (privilege-escalation-by-tampering guard).
* :class:`HouseholdCredentialRecord`, :class:`InstanceSystemCredentialRecord`
  — frozen dataclasses returned by metadata reads. NEVER carry
  ciphertext or plaintext.
* CRUD verbs per tier (``household_*`` and ``system_*``):
  ``get_record``, ``list_records``, ``get_payload``, ``put_payload``,
  ``delete_payload``.
* :func:`household_count_rows_by_key_version`,
  :func:`system_count_rows_by_key_version` — diagnostic counters
  used by ``GET /api/v1/admin/diagnostics/credential-key-fingerprint``.
* :func:`reencrypt_household_to_current_version`,
  :func:`reencrypt_system_to_current_version` — operator rotation
  driven by :meth:`cryptography.fernet.MultiFernet.rotate`. Called
  by :func:`services.connector_credentials.reencrypt_all_to_current_version`
  via a lazy import (the import direction is one-way: this module
  imports the action-name constants from ``connector_credentials``
  at top level; that module imports back into this one only inside
  the rotation function body).

Registry-strictness
-------------------
Mirrors the per-user table's contract (see
:mod:`services.connector_credentials`):

================== ===================== ===========================
Function           ``validate_format``   ``require_registered``
================== ===================== ===========================
*_put_payload      yes                   **yes**
*_get_payload      yes                   **yes**
*_get_record       yes                   no
*_list_records     n/a                   no
*_delete_payload   yes                   no
================== ===================== ===========================

Audit attribution
-----------------
For household + system tier writes, ``audit_log.user_id`` is the
**acting admin's** ``user_id`` (parsed from the ``admin:<id>`` actor
string), or the literal ``"default"`` for ``actor="system"`` (the
rotation script and migration callers). ``input_summary`` carries
``{actor, key_version, tier}`` where ``tier`` is the family
discriminator. This is intentionally different from the per-user
contract, which preserves the row-owner's ``user_id`` in the audit
row — see plan §Audit-attribution edge cases.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from typing import Literal

from connectors import registry as connectors_registry
from services._credential_internals import ConnectorNotConfigured
from services._credential_internals import CredentialUnavailable
from services._credential_internals import _actor_str_tiered
from services._credential_internals import _current_key_version
from services._credential_internals import _decrypt_payload
from services._credential_internals import _emit_audit
from services._credential_internals import _encrypt_payload
from services._credential_internals import _get_multifernet
from services._credential_internals import _resolve_env_fallback
from services.connector_credentials import ACTION_CRED_DELETED
from services.connector_credentials import ACTION_CRED_PUT
from services.connector_credentials import ACTION_CRED_ROTATED

import config

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Records — frozen dataclasses returned by metadata paths.
# Mirror the Pydantic public models 1:1 so route code can do
# ``HouseholdConnectorCredentialPublic.model_validate(record.__dict__)``.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HouseholdCredentialRecord:
    """Row metadata from ``household_connector_credentials``.

    NEVER carries ciphertext or plaintext.
    """

    connector: str
    created_at: datetime
    updated_at: datetime
    created_by: str
    updated_by: str
    key_version: int


@dataclass(frozen=True)
class InstanceSystemCredentialRecord:
    """Row metadata from ``instance_system_connector_credentials``.

    NEVER carries ciphertext or plaintext.
    """

    connector: str
    created_at: datetime
    updated_at: datetime
    created_by: str
    updated_by: str
    key_version: int


# ---------------------------------------------------------------------------
# ResolvedCredential — runtime resolver return type.
# ``__repr__`` is overridden to keep plaintext out of debug logs.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolvedCredential:
    """Runtime resolver result.

    ``payload`` is the plaintext credential dict (the same shape
    returned by :func:`services.connector_credentials.resolve` and the
    tier ``*_get_payload`` helpers). ``tier`` records WHICH tier
    actually supplied the payload — useful for runtime telemetry and
    for callers that want to surface "this is a household-shared
    credential" UX hints.

    The ``__repr__`` override is the defence-in-depth safety hygiene
    pin from plan §`ResolvedCredential` D5.5 — without it, a stray
    ``f"... {resolved}"`` in a debug log line would dump the
    plaintext API key into the operator's terminal. Test #44a
    asserts ``"<redacted" in repr(rc)`` and the secret value is not
    present. Note that ``services.caldav_credentials.CaldavConnection``
    does NOT currently implement this — the precedent is being
    established here.
    """

    payload: dict[str, Any]
    tier: Literal["user", "household", "system", "env"]

    def __repr__(self) -> str:  # pragma: no cover — trivial format
        return f"ResolvedCredential(tier={self.tier!r}, payload=<redacted len={len(self.payload)}>)"


# ---------------------------------------------------------------------------
# Column projections + record constructors.
# ---------------------------------------------------------------------------


_HOUSEHOLD_SELECT_COLS = "connector, created_at, updated_at, created_by, updated_by, key_version"
_SYSTEM_SELECT_COLS = _HOUSEHOLD_SELECT_COLS  # same shape, distinct name kept for grep


def _household_record_from_row(row: dict) -> HouseholdCredentialRecord:
    """Build a :class:`HouseholdCredentialRecord` from a SELECT result dict."""
    return HouseholdCredentialRecord(
        connector=row["connector"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        created_by=row["created_by"],
        updated_by=row["updated_by"],
        key_version=row["key_version"],
    )


def _system_record_from_row(row: dict) -> InstanceSystemCredentialRecord:
    """Build an :class:`InstanceSystemCredentialRecord` from a SELECT result dict."""
    return InstanceSystemCredentialRecord(
        connector=row["connector"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        created_by=row["created_by"],
        updated_by=row["updated_by"],
        key_version=row["key_version"],
    )


# ---------------------------------------------------------------------------
# Audit attribution helpers.
# ---------------------------------------------------------------------------


_ADMIN_ACTOR_RE = re.compile(r"^admin:([A-Za-z0-9_\-]{1,64})$")


def _extract_admin_caller_user_id(actor: str) -> str:
    """Return the user_id to record in ``audit_log.user_id`` for tier writes.

    * ``actor == "system"`` ⇒ ``"default"`` — matches the
      :class:`models.actions.AuditEntry` ``user_id`` dataclass default,
      so rotation script and migration writes get a consistent
      identity column rather than crashing the rotation script.
    * ``actor == "admin:<id>"`` ⇒ ``<id>`` — the acting admin's
      ``user_id``. The ``admin:`` prefix is stripped because
      ``audit_log.user_id`` is a free-form text column that other
      audit consumers expect to hold a bare user_id.
    * Anything else ⇒ :class:`ValueError`. Caller MUST have already
      passed the actor through :func:`_actor_str_tiered`, which
      rejects ``"self"`` and any malformed value, so reaching the
      ``ValueError`` branch indicates a programming error.

    Used by tier put/delete to populate ``audit_log.user_id`` before
    calling :func:`_emit_audit`, AND by
    :func:`reencrypt_household_to_current_version` /
    :func:`reencrypt_system_to_current_version` so rotation audit
    rows get ``user_id="default"`` under ``actor="system"``.
    """
    if actor == "system":
        return "default"
    m = _ADMIN_ACTOR_RE.match(actor)
    if not m:
        raise ValueError(
            f"_extract_admin_caller_user_id: unparseable actor {actor!r} "
            f"(must be 'system' or 'admin:<id>')"
        )
    return m.group(1)


def _validate_tier_actor(actor: str, *, call_site: str) -> str:
    """Validate ``actor`` against ``_ACTOR_RE_TIERED`` and log on rejection.

    Wraps :func:`_actor_str_tiered` so the structured WARN log fires
    once per rejection (per plan §`_actor_str_tiered` rejection
    rule). The ``ValueError`` bubbles up unchanged — callers translate
    to HTTP 400 at the route layer or re-raise inside the service.
    """
    try:
        return _actor_str_tiered(actor)
    except ValueError:
        _log.warning(
            "credential_tiers.invalid_actor actor_repr=%r call_site=%s",
            repr(actor)[:64],
            call_site,
        )
        raise


# ---------------------------------------------------------------------------
# Household tier — CRUD.
# ---------------------------------------------------------------------------


def household_get_record(connector: str) -> HouseholdCredentialRecord | None:
    """Return household row metadata, ``None`` on miss.

    Format-strict, NOT registry-strict — operators can read metadata
    of historical-but-still-stored rows whose connector id has since
    been removed from the canonical registry mapping.
    """
    connectors_registry.validate_format(connector)
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        f"SELECT {_HOUSEHOLD_SELECT_COLS} FROM household_connector_credentials "
        "WHERE connector = %s",
        (connector,),
    )
    return _household_record_from_row(row) if row else None


def household_list_records() -> list[HouseholdCredentialRecord]:
    """Enumerate every household row, ordered by ``connector ASC``.

    No registry filtering — historical-but-still-stored connectors
    appear so the admin UI can offer a delete affordance for stale
    rows.
    """
    ms = config.get_metadata_store()
    rows = ms.fetch_all(
        f"SELECT {_HOUSEHOLD_SELECT_COLS} FROM household_connector_credentials "
        "ORDER BY connector ASC"
    )
    return [_household_record_from_row(r) for r in rows]


def household_get_payload(connector: str) -> dict[str, Any] | None:
    """Decrypt + return a household JSON payload, ``None`` on miss.

    Plaintext-bearing path; **registry-strict** (calls
    :func:`connectors.registry.require_registered`). Operators reading
    metadata for a stale row use :func:`household_get_record` instead.
    Decrypt failure ⇒ :class:`CredentialUnavailable`.
    """
    connectors_registry.require_registered(connector)
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT ciphertext FROM household_connector_credentials WHERE connector = %s",
        (connector,),
    )
    if row is None:
        return None
    return _decrypt_payload(row["ciphertext"])


def household_put_payload(
    connector: str,
    payload: dict[str, Any],
    *,
    actor: str,
) -> HouseholdCredentialRecord:
    """UPSERT a household row; emit audit; return the fresh record.

    Registry-strict. ``audit_log.user_id`` carries the acting admin's
    bare ``user_id`` (or ``"default"`` for ``actor="system"``);
    ``input_summary`` carries ``tier="household"``.
    """
    connectors_registry.require_registered(connector)
    actor_clean = _validate_tier_actor(actor, call_site="household_put_payload")
    audit_user_id = _extract_admin_caller_user_id(actor_clean)
    ciphertext = _encrypt_payload(payload)
    key_version = _current_key_version()

    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "INSERT INTO household_connector_credentials "
        "(connector, ciphertext, key_version, created_by, updated_by) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (connector) DO UPDATE SET "
        "  ciphertext = EXCLUDED.ciphertext, "
        "  key_version = EXCLUDED.key_version, "
        "  updated_at = NOW(), "
        "  updated_by = EXCLUDED.updated_by "
        f"RETURNING {_HOUSEHOLD_SELECT_COLS}",
        (connector, ciphertext, key_version, actor_clean, actor_clean),
    )
    if row is None:
        raise RuntimeError(
            f"household_put_payload: row not visible after upsert connector={connector!r}"
        )
    record = _household_record_from_row(row)
    _emit_audit(
        audit_user_id,
        connector,
        ACTION_CRED_PUT,
        actor=actor_clean,
        key_version=key_version,
        tier="household",
    )
    _log.info(
        "credential_tiers: household put connector=%s actor=%s",
        connector,
        actor_clean,
    )
    return record


def household_delete_payload(connector: str, *, actor: str) -> bool:
    """Delete a household row; emit audit on hit; return True iff a row was deleted.

    Format-strict only — operators can clean up rows whose connector
    id has since been removed from the canonical registry mapping.
    """
    connectors_registry.validate_format(connector)
    actor_clean = _validate_tier_actor(actor, call_site="household_delete_payload")
    audit_user_id = _extract_admin_caller_user_id(actor_clean)

    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "DELETE FROM household_connector_credentials WHERE connector = %s RETURNING key_version",
        (connector,),
    )
    if row is None:
        return False
    _emit_audit(
        audit_user_id,
        connector,
        ACTION_CRED_DELETED,
        actor=actor_clean,
        key_version=row["key_version"],
        tier="household",
    )
    _log.info(
        "credential_tiers: household deleted connector=%s actor=%s",
        connector,
        actor_clean,
    )
    return True


def household_count_rows_by_key_version() -> dict[int, int]:
    """Group household rows by ``key_version`` for the diagnostics endpoint."""
    ms = config.get_metadata_store()
    rows = ms.fetch_all(
        "SELECT key_version, COUNT(*) AS n "
        "FROM household_connector_credentials "
        "GROUP BY key_version "
        "ORDER BY key_version"
    )
    return {int(r["key_version"]): int(r["n"]) for r in rows}


# ---------------------------------------------------------------------------
# Instance/system tier — CRUD (mirrors household with renamed table).
# ---------------------------------------------------------------------------


def system_get_record(connector: str) -> InstanceSystemCredentialRecord | None:
    """Return instance/system row metadata, ``None`` on miss."""
    connectors_registry.validate_format(connector)
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        f"SELECT {_SYSTEM_SELECT_COLS} FROM instance_system_connector_credentials "
        "WHERE connector = %s",
        (connector,),
    )
    return _system_record_from_row(row) if row else None


def system_list_records() -> list[InstanceSystemCredentialRecord]:
    """Enumerate every instance/system row, ordered by ``connector ASC``."""
    ms = config.get_metadata_store()
    rows = ms.fetch_all(
        f"SELECT {_SYSTEM_SELECT_COLS} FROM instance_system_connector_credentials "
        "ORDER BY connector ASC"
    )
    return [_system_record_from_row(r) for r in rows]


def system_get_payload(connector: str) -> dict[str, Any] | None:
    """Decrypt + return an instance/system JSON payload, ``None`` on miss."""
    connectors_registry.require_registered(connector)
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT ciphertext FROM instance_system_connector_credentials WHERE connector = %s",
        (connector,),
    )
    if row is None:
        return None
    return _decrypt_payload(row["ciphertext"])


def system_put_payload(
    connector: str,
    payload: dict[str, Any],
    *,
    actor: str,
) -> InstanceSystemCredentialRecord:
    """UPSERT an instance/system row; emit audit; return the fresh record."""
    connectors_registry.require_registered(connector)
    actor_clean = _validate_tier_actor(actor, call_site="system_put_payload")
    audit_user_id = _extract_admin_caller_user_id(actor_clean)
    ciphertext = _encrypt_payload(payload)
    key_version = _current_key_version()

    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "INSERT INTO instance_system_connector_credentials "
        "(connector, ciphertext, key_version, created_by, updated_by) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (connector) DO UPDATE SET "
        "  ciphertext = EXCLUDED.ciphertext, "
        "  key_version = EXCLUDED.key_version, "
        "  updated_at = NOW(), "
        "  updated_by = EXCLUDED.updated_by "
        f"RETURNING {_SYSTEM_SELECT_COLS}",
        (connector, ciphertext, key_version, actor_clean, actor_clean),
    )
    if row is None:
        raise RuntimeError(
            f"system_put_payload: row not visible after upsert connector={connector!r}"
        )
    record = _system_record_from_row(row)
    _emit_audit(
        audit_user_id,
        connector,
        ACTION_CRED_PUT,
        actor=actor_clean,
        key_version=key_version,
        tier="system",
    )
    _log.info(
        "credential_tiers: system put connector=%s actor=%s",
        connector,
        actor_clean,
    )
    return record


def system_delete_payload(connector: str, *, actor: str) -> bool:
    """Delete an instance/system row; emit audit on hit; return True iff a row was deleted."""
    connectors_registry.validate_format(connector)
    actor_clean = _validate_tier_actor(actor, call_site="system_delete_payload")
    audit_user_id = _extract_admin_caller_user_id(actor_clean)

    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "DELETE FROM instance_system_connector_credentials "
        "WHERE connector = %s "
        "RETURNING key_version",
        (connector,),
    )
    if row is None:
        return False
    _emit_audit(
        audit_user_id,
        connector,
        ACTION_CRED_DELETED,
        actor=actor_clean,
        key_version=row["key_version"],
        tier="system",
    )
    _log.info(
        "credential_tiers: system deleted connector=%s actor=%s",
        connector,
        actor_clean,
    )
    return True


def system_count_rows_by_key_version() -> dict[int, int]:
    """Group instance/system rows by ``key_version`` for the diagnostics endpoint."""
    ms = config.get_metadata_store()
    rows = ms.fetch_all(
        "SELECT key_version, COUNT(*) AS n "
        "FROM instance_system_connector_credentials "
        "GROUP BY key_version "
        "ORDER BY key_version"
    )
    return {int(r["key_version"]): int(r["n"]) for r in rows}


# ---------------------------------------------------------------------------
# Operator rotation — household + instance/system.
# Called by services.connector_credentials.reencrypt_all_to_current_version
# via a lazy import (preserves the one-way import direction documented at
# module top).
# ---------------------------------------------------------------------------


def _reencrypt_tier_table(
    *,
    table: str,
    tier_label: Literal["household", "system"],
    actor_clean: str,
) -> dict[str, int]:
    """Walk ``table``, re-seal stale rows, return per-tier counters.

    Skip predicate: ``row.key_version == _current_key_version()``.
    Per-row defence-in-depth: ``rotate`` then ``decrypt`` to verify
    before issuing the UPDATE — any failure short-circuits and counts
    as ``failed``. A failure on row N never rolls back rows 1..N-1.
    """
    mf = _get_multifernet()
    current_fp = _current_key_version()
    audit_user_id = _extract_admin_caller_user_id(actor_clean)

    ms = config.get_metadata_store()
    rows = ms.fetch_all(
        f"SELECT connector, ciphertext, key_version FROM {table} ORDER BY connector ASC"
    )

    rotated = 0
    skipped = 0
    failed = 0

    for row in rows:
        connector = row["connector"]
        old_ciphertext = bytes(row["ciphertext"])

        if row["key_version"] == current_fp:
            skipped += 1
            continue

        try:
            new_token = mf.rotate(old_ciphertext)
        except Exception as exc:
            _log.error(
                "rotate failed for %s connector=%s (%s); ciphertext bytes NOT logged",
                tier_label,
                connector,
                exc.__class__.__name__,
            )
            failed += 1
            continue

        try:
            mf.decrypt(new_token)
        except Exception as exc:
            _log.error(
                "rotate verify failed for %s connector=%s (%s); ciphertext bytes NOT logged",
                tier_label,
                connector,
                exc.__class__.__name__,
            )
            failed += 1
            continue

        try:
            ms.execute(
                f"UPDATE {table} "
                f"SET ciphertext = %s, key_version = %s, "
                f"    updated_at = NOW(), updated_by = %s "
                f"WHERE connector = %s",
                (new_token, current_fp, actor_clean, connector),
            )
        except Exception as exc:
            _log.error(
                "rotate UPDATE failed for %s connector=%s (%s)",
                tier_label,
                connector,
                exc.__class__.__name__,
            )
            failed += 1
            continue

        _emit_audit(
            audit_user_id,
            connector,
            ACTION_CRED_ROTATED,
            actor=actor_clean,
            key_version=current_fp,
            tier=tier_label,
        )
        rotated += 1

    return {"rotated": rotated, "skipped": skipped, "failed": failed}


def reencrypt_household_to_current_version(
    *,
    actor: str = "system",
) -> dict[str, int]:
    """Re-encrypt household rows to the current primary key.

    Returns ``{"rotated": N, "skipped": N, "failed": N}`` — the same
    shape as the per-user counterpart. Aggregated by
    :func:`services.connector_credentials.reencrypt_all_to_current_version`
    into the cross-tier ``by_tier`` summary.
    """
    actor_clean = _validate_tier_actor(
        actor,
        call_site="reencrypt_household_to_current_version",
    )
    return _reencrypt_tier_table(
        table="household_connector_credentials",
        tier_label="household",
        actor_clean=actor_clean,
    )


def reencrypt_system_to_current_version(
    *,
    actor: str = "system",
) -> dict[str, int]:
    """Re-encrypt instance/system rows to the current primary key."""
    actor_clean = _validate_tier_actor(
        actor,
        call_site="reencrypt_system_to_current_version",
    )
    return _reencrypt_tier_table(
        table="instance_system_connector_credentials",
        tier_label="system",
        actor_clean=actor_clean,
    )


# ---------------------------------------------------------------------------
# Runtime resolver — user → household → system → env precedence.
# ---------------------------------------------------------------------------


# Only these exception classes are considered "well-defined misses /
# faults". Anything else escapes after a structured log so the fault
# domain stays bounded but observable (per plan §Exception contract).
_RESOLVER_DOMAIN_EXCEPTIONS: tuple[type[BaseException], ...] = (
    connectors_registry.UnknownConnector,
    ConnectorNotConfigured,
    CredentialUnavailable,
    ValueError,
)


def _resolve_caller_user_id(caller_user_id: Any) -> str:
    """Validate and return ``caller_user_id``.

    Empty / whitespace-only / ``None`` ⇒ :class:`ValueError` (per
    plan §Resolver caller_user_id validation, D6.7). Runs **before**
    :func:`connectors.registry.require_registered` so misuse surfaces
    with a precise diagnostic rather than a confusing
    ``UnknownConnector``.
    """
    if not isinstance(caller_user_id, str) or not caller_user_id.strip():
        raise ValueError("caller_user_id required")
    return caller_user_id


def resolve_runtime_credential(
    caller_user_id: str,
    connector: str,
    *,
    fallback_env: str | None = None,
) -> ResolvedCredential:
    """Walk the credential tier precedence and return a :class:`ResolvedCredential`.

    Precedence: ``user → household → system → env``. The env fallback
    is delegated to :func:`services._credential_internals._resolve_env_fallback`
    so the rules match :func:`services.connector_credentials.resolve`
    byte-for-byte.

    **Decrypt-failure semantics (security-critical):** decrypt failure
    on **any** tier surfaces :class:`CredentialUnavailable`
    immediately. The resolver does NOT fall through to the next tier
    on decrypt failure. Only :class:`ConnectorNotConfigured` (row not
    present) advances the walk. This prevents a "tamper-to-downgrade"
    attack where corrupting a user-tier ciphertext would silently
    substitute household-tier material.

    **Registry strictness (defence in depth, intentional):** this
    function calls :func:`require_registered` itself AND the
    underlying ``ccs.get_payload`` / household / system reads also
    call it. Do NOT refactor one of them away to "deduplicate" — the
    double check is intentional.

    **Per-user tier delegation:** the per-user tier read calls
    existing :func:`services.connector_credentials.get_payload` —
    does NOT duplicate the SQL.

    **Exception contract:** per-tier reads catch only
    :class:`UnknownConnector`, :class:`ConnectorNotConfigured`,
    :class:`CredentialUnavailable`, and :class:`ValueError` and
    re-raise them per the documented HTTP mapping. Any **other**
    exception class is logged at ``ERROR`` level (with structured
    fields) and re-raised — bounded fault domain, observable
    failures, no blanket ``except Exception: pass``.
    """
    caller_clean = _resolve_caller_user_id(caller_user_id)
    connectors_registry.require_registered(connector)

    # Tier 1 — per-user. Delegate to the existing service module so
    # the SQL stays in one place.
    from services import (
        connector_credentials as ccs,  # local to keep import direction one-way at top
    )

    try:
        user_payload = ccs.get_payload(caller_clean, connector)
    except _RESOLVER_DOMAIN_EXCEPTIONS:
        raise
    except Exception as exc:
        _log.error(
            "resolve_runtime_credential.unexpected_exception tier=%s connector=%s exc_class=%s",
            "user",
            connector,
            exc.__class__.__name__,
            exc_info=True,
        )
        raise
    if user_payload is not None:
        return ResolvedCredential(payload=user_payload, tier="user")

    # Tier 2 — household.
    try:
        household_payload = household_get_payload(connector)
    except _RESOLVER_DOMAIN_EXCEPTIONS:
        raise
    except Exception as exc:
        _log.error(
            "resolve_runtime_credential.unexpected_exception tier=%s connector=%s exc_class=%s",
            "household",
            connector,
            exc.__class__.__name__,
            exc_info=True,
        )
        raise
    if household_payload is not None:
        return ResolvedCredential(payload=household_payload, tier="household")

    # Tier 3 — instance/system.
    try:
        system_payload = system_get_payload(connector)
    except _RESOLVER_DOMAIN_EXCEPTIONS:
        raise
    except Exception as exc:
        _log.error(
            "resolve_runtime_credential.unexpected_exception tier=%s connector=%s exc_class=%s",
            "system",
            connector,
            exc.__class__.__name__,
            exc_info=True,
        )
        raise
    if system_payload is not None:
        return ResolvedCredential(payload=system_payload, tier="system")

    # Tier 4 — env fallback (shared canonical helper; same rules as
    # ``services.connector_credentials.resolve``).
    env_payload = _resolve_env_fallback(connector, fallback_env)
    if env_payload is not None:
        return ResolvedCredential(payload=env_payload, tier="env")

    raise ConnectorNotConfigured(
        f"no credential row at any tier and no usable env fallback for "
        f"caller_user_id={caller_clean!r} connector={connector!r}"
    )


__all__ = [
    "ResolvedCredential",
    "HouseholdCredentialRecord",
    "InstanceSystemCredentialRecord",
    "household_get_record",
    "household_list_records",
    "household_get_payload",
    "household_put_payload",
    "household_delete_payload",
    "household_count_rows_by_key_version",
    "system_get_record",
    "system_list_records",
    "system_get_payload",
    "system_put_payload",
    "system_delete_payload",
    "system_count_rows_by_key_version",
    "reencrypt_household_to_current_version",
    "reencrypt_system_to_current_version",
    "resolve_runtime_credential",
]
