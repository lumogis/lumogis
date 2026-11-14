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
  :data:`ACTION_CRED_DELETED`, :data:`ACTION_CRED_ROTATED`,
  :data:`ACTION_CRED_TESTED`, :data:`ACTION_CRED_ENABLED`) — the
  tier sibling imports them from here; the import direction is
  one-way (this file never imports ``credential_tiers`` at module
  top, only inside :func:`reencrypt_all_to_current_version` to walk
  the household + system tables);
* keeps every public verb (``get_record``, ``list_records``,
  ``get_payload``, ``put_payload``, ``delete_payload``, ``resolve``,
  ``reencrypt_all_to_current_version``, ``count_rows_by_key_version``,
  ``set_delivery_paused``, ``set_enabled``, ``is_disabled``,
  ``register_change_listener``, ``reset_listeners_for_tests``)
  exactly as before. Callers do not see the internal split.

Public surface
--------------
* :class:`CredentialRecord` — frozen dataclass mirroring a row's DB
  metadata **including pause diagnostics** (:attr:`delivery_paused_detail`).
  It is **not** 1:1 with :class:`~models.connector_credential.ConnectorCredentialPublic`
  (the wire shape omits ``delivery_paused_detail``). Routes must not use
  ``ConnectorCredentialPublic.model_validate(record.__dict__)`` verbatim.
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
Each verb uses ``validate_format`` and/or ``require_registered`` as follows:

* ``put_payload`` — validate yes, require registered **yes** —
  cannot create rows for unknown connectors.
* ``get_payload`` — validate yes, require registered **yes** — plaintext path; runtime-style use.
* ``resolve`` — validate yes, require registered **yes** — runtime path; fail-closed.
* ``get_record`` — validate yes, require registered no — admin metadata on stale rows.
* ``list_records`` — n/a, require registered no — operator visibility includes historical rows.
* ``delete_payload`` — validate yes, require registered no — admin cleanup of stale rows.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from connectors import registry as connectors_registry
from connectors.registry import UnknownConnector  # noqa: F401 — re-exported for callers
from services._credential_internals import (  # noqa: F401 — _-prefixed names re-exported for back-compat with pre-refactor test imports
    _PLACEHOLDER_KEYS,
)
from services._credential_internals import (  # noqa: F401 — _-prefixed names re-exported for back-compat with pre-refactor test imports
    ConnectorNotConfigured,
)
from services._credential_internals import (  # noqa: F401 — _-prefixed names re-exported for back-compat with pre-refactor test imports
    CredentialUnavailable,
)
from services._credential_internals import (  # noqa: F401 — _-prefixed names re-exported for back-compat with pre-refactor test imports
    _actor_str,
)
from services._credential_internals import (  # noqa: F401 — _-prefixed names re-exported for back-compat with pre-refactor test imports
    _build_multifernet,
)
from services._credential_internals import (  # noqa: F401 — _-prefixed names re-exported for back-compat with pre-refactor test imports
    _current_key_version,
)
from services._credential_internals import (  # noqa: F401 — _-prefixed names re-exported for back-compat with pre-refactor test imports
    _decrypt_payload,
)
from services._credential_internals import (  # noqa: F401 — _-prefixed names re-exported for back-compat with pre-refactor test imports
    _emit_audit as _emit_audit_internal,
)
from services._credential_internals import (  # noqa: F401 — _-prefixed names re-exported for back-compat with pre-refactor test imports
    _encrypt_payload,
)
from services._credential_internals import (  # noqa: F401 — _-prefixed names re-exported for back-compat with pre-refactor test imports
    _get_multifernet,
)
from services._credential_internals import (  # noqa: F401 — _-prefixed names re-exported for back-compat with pre-refactor test imports
    _key_fingerprint,
)
from services._credential_internals import (  # noqa: F401 — _-prefixed names re-exported for back-compat with pre-refactor test imports
    _load_keys,
)
from services._credential_internals import (  # noqa: F401 — _-prefixed names re-exported for back-compat with pre-refactor test imports
    _resolve_env_fallback,
)
from services._credential_internals import (  # noqa: F401 — _-prefixed names re-exported for back-compat with pre-refactor test imports
    get_current_key_version,
)
from services._credential_internals import (  # noqa: F401 — _-prefixed names re-exported for back-compat with pre-refactor test imports
    reset_for_tests,
)

import config

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
#: LUM-834 — a test-connection ran against a stored credential. Records THAT a
#: test happened and whether it passed; never what it was given. Emitted by
#: :mod:`services.connector_probes`, and only for outcomes that actually
#: attempted a connection (see its ``_audit``).
ACTION_CRED_TESTED = "__connector_credential__.tested"
#: LUM-834 chunk B — a user switched a stored credential on or off. The NEW
#: STATE lives in ``input_summary["enabled"]``, never in ``result_summary.ok``,
#: which keeps meaning "the write succeeded" here as it does for every other
#: action in this vocabulary. Emitted by :func:`set_enabled`, in the service,
#: per the route file's stated convention.
ACTION_CRED_ENABLED = "__connector_credential__.enabled"


# ---------------------------------------------------------------------------
# CredentialRecord — public dataclass returned by every metadata path.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CredentialRecord:
    """Row metadata returned by every metadata read path.

    Carries **delivery pause** diagnostics from
    ``user_connector_credentials`` (:attr:`delivery_paused_detail`).
    The HTTP wire model :class:`~models.connector_credential.ConnectorCredentialPublic`
    **omits** ``delivery_paused_detail`` — use the route helper that
    builds an explicit dict projection.

    NEVER carries ciphertext or plaintext.
    """

    user_id: str
    connector: str
    created_at: datetime
    updated_at: datetime
    created_by: str
    updated_by: str
    key_version: int
    delivery_paused: bool = False
    delivery_paused_reason: str | None = None
    delivery_paused_detail: str | None = None
    delivery_paused_at: datetime | None = None
    # LUM-170: instance discriminator for multi-instance connectors (imap keys
    # per-mailbox by source_id). Empty for single-instance connectors. Placed
    # last so existing positional/keyword construction is unaffected.
    instance: str = ""
    # LUM-834 chunk B. User-set on/off, distinct from ``delivery_paused``
    # (system-set on delivery failure). Effective state is
    # ``enabled and not delivery_paused``.
    enabled: bool = True
    #: Payload key NAMES from the last PUT — never a value.
    #:
    #: ``None`` means UNKNOWN, not "no keys": the row predates migration 079
    #: and cannot be populated without decrypting it. Callers must NOT coerce
    #: it to ``[]``; a working connector would then render as unconfigured,
    #: which is ADR-199's failure mode by another route. Populated by the next
    #: :func:`put_payload`. For an OAuth IMAP row that is the next **successful**
    #: token refresh, so a pre-079 OAuth row that keeps failing stays unknown;
    #: any other row heals only when it is saved again.
    #:
    #: ``GET /sources`` derives ``auth_method`` from this field (LUM-777, ADR-225).
    configured_keys: list[str] | None = None


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
    input_extra: dict[str, Any] | None = None,
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
        input_extra=input_extra,
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

    where ``action`` is ``"put"``, ``"delete"`` or ``"enabled"``. Listeners run inline
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


def _fire_change(user_id: str, connector: str, *, action: str, instance: str = "") -> None:
    """Invoke every registered listener with the ``instance`` (LUM-170); never raises.

    Listeners accept ``(*, user_id, connector, action, instance="")`` — the
    ``instance`` default keeps any listener that does not care about it working
    without a per-call exception dance on the write path.
    """
    with _LISTENERS_LOCK:
        listeners = list(_LISTENERS)
    for listener in listeners:
        try:
            listener(user_id=user_id, connector=connector, action=action, instance=instance)
        except Exception:
            _log.exception(
                "connector_credentials change listener %r raised "
                "(user_id=%s connector=%s action=%s); ignoring",
                listener,
                user_id,
                connector,
                action,
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
        delivery_paused=bool(row.get("delivery_paused", False)),
        delivery_paused_reason=row.get("delivery_paused_reason"),
        delivery_paused_detail=row.get("delivery_paused_detail"),
        delivery_paused_at=row.get("delivery_paused_at"),
        instance=row.get("instance", "") or "",
        enabled=bool(row.get("enabled", True)),
        # Deliberately NOT `or []`, and deliberately not defaulted through
        # `row.get(..., [])`: NULL is a real state (unknown) and the line
        # directly above — `or ""` on instance — is exactly the coercion this
        # field must not receive. A pre-migration row must reach the wire as
        # None so the card can say "configured, details after the next save"
        # rather than "nothing set".
        configured_keys=(
            list(row["configured_keys"]) if row.get("configured_keys") is not None else None
        ),
    )


# LUM-170: instance validation. A non-empty instance is allowed iff the
# connector is declared multi-instance in the registry; a multi-instance
# connector requires a non-empty instance on writes. Reads/deletes tolerate
# either (operator cleanup of historical rows). Format matches the DB CHECK
# ``^[a-z0-9_\-]{0,64}$``.
_INSTANCE_FORMAT = re.compile(r"^[a-z0-9_\-]{0,64}$")


class InstanceNotAllowed(ValueError):
    """A non-empty instance was supplied for a single-instance connector,
    or an empty instance for a multi-instance connector on a write."""


def _validate_instance(connector: str, instance: str, *, write: bool) -> None:
    if not _INSTANCE_FORMAT.match(instance or ""):
        raise InstanceNotAllowed(f"instance must match {_INSTANCE_FORMAT.pattern!r}")
    is_multi = connectors_registry.is_multi_instance(connector)
    if instance and not is_multi:
        raise InstanceNotAllowed(
            f"connector {connector!r} is single-instance; instance must be empty"
        )
    if write and is_multi and not instance:
        raise InstanceNotAllowed(
            f"connector {connector!r} is multi-instance; a non-empty instance is required"
        )


# ---------------------------------------------------------------------------
# Public API — metadata reads (format-strict, NOT registry-strict)
# ---------------------------------------------------------------------------


_SELECT_RECORD_COLS = (
    "user_id, connector, instance, created_at, updated_at, "
    "created_by, updated_by, key_version, "
    "delivery_paused, delivery_paused_reason, "
    "delivery_paused_detail, delivery_paused_at, "
    "enabled, configured_keys"
)


def get_record(user_id: str, connector: str, *, instance: str = "") -> CredentialRecord | None:
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
    # authored memory; the (user_id, connector, instance) primary key already
    # makes the row uniquely owned by exactly one user.
    row = ms.fetch_one(
        f"SELECT {_SELECT_RECORD_COLS} FROM user_connector_credentials "
        "WHERE user_id = %s AND connector = %s AND instance = %s",
        (user_id, connector, instance),
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
        "WHERE user_id = %s ORDER BY connector ASC, instance ASC",
        (user_id,),
    )
    return [_record_from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Public API — plaintext reads (registry-strict)
# ---------------------------------------------------------------------------


def get_payload(user_id: str, connector: str, *, instance: str = "") -> dict[str, Any] | None:
    """Decrypt + return the JSON payload, ``None`` on miss.

    Plaintext-bearing path; **registry-strict** by design (calls
    :func:`connectors.registry.require_registered`). Operators reading
    metadata for a stale row use :func:`get_record` instead, which
    does NOT require registration.

    ``instance`` selects a specific row of a multi-instance connector
    (LUM-170; imap keys per-mailbox by ``source_id``). Defaults to ``""``
    (the single-instance row), preserving every existing caller.

    **A disabled row yields no plaintext** (LUM-834 chunk B): the SELECT
    carries ``AND enabled``, so a user who turns a connector off gets the
    same ``None`` as a user who never configured one. That is the security
    half of the gate and it is unconditional. Callers that distinguish
    *disabled* from *absent* — the tier walk, the loaders' env fallbacks —
    ask :func:`is_disabled` afterwards; ``None`` alone cannot tell them apart.
    """
    connectors_registry.require_registered(connector)
    _validate_instance(connector, instance, write=False)
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: user_connector_credentials has no `scope` column
    # — encrypted per-user external-service secrets, not authored memory.
    row = ms.fetch_one(
        "SELECT ciphertext FROM user_connector_credentials "
        "WHERE user_id = %s AND connector = %s AND instance = %s AND enabled",
        (user_id, connector, instance),
    )
    if row is None:
        return None
    return _decrypt_payload(row["ciphertext"])


def is_disabled(user_id: str, connector: str, *, instance: str = "") -> bool:
    """Is there a row for this credential, and is it switched off? (LUM-834 B)

    Exists because ``get_payload`` returning ``None`` is **one bit for two
    questions**. Once the gate lands, *disabled* and *absent* are identical to
    every caller of ``get_payload`` — and they must produce opposite behaviour:
    an absent user row falls through to the household tier and lets a loader
    use its env fallback; a disabled one must do neither.

    Contract, stated rather than left to be inferred from a test:

    * **An absent row is** ``False``, **not** ``True``. A connector that was
      never configured is not "disabled"; saying so would stop the tier walk
      for every user who has no row, which is most of them.
    * **Registry-strict**, like every other verb that names a live connector.
    * **Instance-aware** — the multi-instance loaders guard their
      ``instance == ""`` branch and need to ask about that row specifically.

    One indexed lookup on the primary key. Called on the resolve path only
    when the payload read has already missed, and inside each loader's
    env-fallback branch before it falls back.
    """
    connectors_registry.require_registered(connector)
    _validate_instance(connector, instance, write=False)
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: user_connector_credentials has no `scope` column
    # — encrypted per-user external-service secrets, not authored memory.
    row = ms.fetch_one(
        "SELECT enabled FROM user_connector_credentials "
        "WHERE user_id = %s AND connector = %s AND instance = %s",
        (user_id, connector, instance),
    )
    if row is None:
        return False
    return not bool(row["enabled"])


def disabled_error(connector: str, *, instance: str = "") -> ConnectorNotConfigured:
    """The exception a disabled connector raises, with a disabled-specific message.

    **Deliberately** :class:`ConnectorNotConfigured` and **not** a new class.
    A sibling exception under ``LookupError`` would not be caught by any
    existing ``except ConnectorNotConfigured``, so it would have escaped from
    the lowest credential primitive into route bodies that do bare truthiness
    checks — a user switching a connector off would have produced a 500 on an
    admin screen. Reusing the exception the codebase already routes correctly
    means chat keeps its 424, ingest keeps skipping, and no consumer changes.

    The message differs so logs can tell the two apart even though the type
    cannot. Note it is only *visible* where the raise reaches the operator:
    ``caldav_credentials`` and ``ntfy_runtime`` catch ``ConnectorNotConfigured``
    from the resolver and raise their own text.
    """
    where = f" instance={instance!r}" if instance else ""
    return ConnectorNotConfigured(
        f"connector {connector!r} is disabled for this user{where} — "
        "the credential is stored but switched off"
    )


# ---------------------------------------------------------------------------
# Public API — writes (registry-strict)
# ---------------------------------------------------------------------------


def put_payload(
    user_id: str,
    connector: str,
    payload: dict[str, Any],
    *,
    actor: str,
    instance: str = "",
) -> CredentialRecord:
    """UPSERT a credential row; emit audit; return the fresh record.

    Registry-strict (calls
    :func:`connectors.registry.require_registered` — unknown
    connectors raise :class:`UnknownConnector` which the route maps
    to HTTP 422). ``created_by`` / ``created_at`` stay first-write;
    ``updated_by`` / ``updated_at`` advance on every call.
    """
    connectors_registry.require_registered(connector)
    _validate_instance(connector, instance, write=True)
    actor_clean = _actor_str(actor)
    ciphertext = _encrypt_payload(payload)
    key_version = _current_key_version()

    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "INSERT INTO user_connector_credentials "
        "(user_id, connector, instance, ciphertext, key_version, created_by, updated_by, "
        " configured_keys) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (user_id, connector, instance) DO UPDATE SET "
        "  ciphertext = EXCLUDED.ciphertext, "
        "  key_version = EXCLUDED.key_version, "
        "  configured_keys = EXCLUDED.configured_keys, "
        "  delivery_paused = false, "
        "  delivery_paused_reason = NULL, "
        "  delivery_paused_detail = NULL, "
        "  delivery_paused_at = NULL, "
        "  updated_at = NOW(), "
        "  updated_by = EXCLUDED.updated_by "
        f"RETURNING {_SELECT_RECORD_COLS}",
        (
            user_id,
            connector,
            instance,
            ciphertext,
            key_version,
            actor_clean,
            actor_clean,
            # Written from the payload being encrypted, in the SAME statement,
            # which is what makes "these two cannot disagree" a property rather
            # than an assertion. On UPDATE it is REPLACED, not merged: a re-PUT
            # with different keys must not leave the old list behind.
            sorted(payload.keys()),
        ),
    )
    if row is None:
        raise RuntimeError(
            f"connector_credentials put: row not visible after upsert "
            f"user_id={user_id!r} connector={connector!r} instance={instance!r}"
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
        "connector_credentials: put user_id=%s connector=%s instance=%s actor=%s",
        user_id,
        connector,
        instance,
        actor_clean,
    )
    _fire_change(user_id, connector, action="put", instance=instance)
    return record


def set_delivery_paused(
    user_id: str,
    connector: str,
    *,
    paused: bool,
    reason: str | None,
    detail: str | None,
    actor: str,
    instance: str = "",
) -> None:
    """Persist delivery-pause metadata for a user-owned credential row only.

    Does **not** read or mutate ``ciphertext``. Used when the ntfy
    upstream rejects delivery (HTTP 410). ``detail`` is truncated to
    512 Unicode chars defensively — storage is ``TEXT`` without DB cap.
    """
    connectors_registry.require_registered(connector)
    actor_clean = _actor_str(actor)
    if detail is not None and len(detail) > 512:
        detail = detail[:512]

    ms = config.get_metadata_store()
    if paused:
        # SCOPE-EXEMPT: credentials table; no scope column; pause by row PK.
        ms.execute(
            "UPDATE user_connector_credentials SET "
            "delivery_paused = true, "
            "delivery_paused_reason = %s, "
            "delivery_paused_detail = %s, "
            "delivery_paused_at = NOW(), "
            "updated_at = NOW(), updated_by = %s "
            # SCOPE-EXEMPT: PK filter on per-user credential row (see block comment above).
            "WHERE user_id = %s AND connector = %s AND instance = %s",
            (
                reason,
                detail,
                actor_clean,
                user_id,
                connector,
                instance,
            ),
        )
    else:
        # SCOPE-EXEMPT: credentials table; no scope column; unpause by row PK.
        ms.execute(
            "UPDATE user_connector_credentials SET "
            "delivery_paused = false, "
            "delivery_paused_reason = NULL, "
            "delivery_paused_detail = NULL, "
            "delivery_paused_at = NULL, "
            "updated_at = NOW(), updated_by = %s "
            # SCOPE-EXEMPT: PK filter on per-user credential row (see block comment above).
            "WHERE user_id = %s AND connector = %s AND instance = %s",
            (actor_clean, user_id, connector, instance),
        )


def set_enabled(
    user_id: str,
    connector: str,
    *,
    enabled: bool,
    actor: str,
    instance: str = "",
) -> CredentialRecord | None:
    """Switch a stored credential on or off. Returns the fresh record, or ``None``.

    Shaped after :func:`set_delivery_paused` — same never-touches-``ciphertext``
    guarantee, one fewer argument — because inventing a different shape beside
    it would be a second convention for the same job. **Disabling does not
    delete anything**: re-enabling resumes without re-entering the secret,
    which is the whole user-facing point of the flag.

    ``None`` means there was no row to switch; the route maps that to 404.

    **Fires the change listeners, and that is not optional.**
    ``set_delivery_paused`` fires none — it predates the listener bus — so
    copying it verbatim would leave ``config``'s per-user LLM adapter cache
    holding a live key after the user switched the connector off, and chat
    would keep working until the next restart. That would falsify the promise
    that disabling stops actions as well as ingest.
    """
    connectors_registry.require_registered(connector)
    _validate_instance(connector, instance, write=False)
    actor_clean = _actor_str(actor)

    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: credentials table; no scope column; flag set by row PK.
    row = ms.fetch_one(
        "UPDATE user_connector_credentials SET "
        "enabled = %s, updated_at = NOW(), updated_by = %s "
        # SCOPE-EXEMPT: PK filter on per-user credential row (see comment above).
        "WHERE user_id = %s AND connector = %s AND instance = %s "
        f"RETURNING {_SELECT_RECORD_COLS}",
        (enabled, actor_clean, user_id, connector, instance),
    )
    if row is None:
        return None

    _emit_audit(
        user_id,
        connector,
        ACTION_CRED_ENABLED,
        actor=actor_clean,
        key_version=_record_from_row(row).key_version,
        # The new state goes in `input_summary`, NOT through the `ok` flag.
        # `ok=false` already means "the write failed" everywhere else in this
        # vocabulary. Overloading it would hand every operator filtering for
        # failed writes a stream of successful disables.
        input_extra={"enabled": bool(enabled)},
    )
    _log.info(
        "connector_credentials: set_enabled user_id=%s connector=%s instance=%s "
        "enabled=%s actor=%s",
        user_id,
        connector,
        instance,
        enabled,
        actor_clean,
    )
    _fire_change(user_id, connector, action="enabled", instance=instance)
    return _record_from_row(row)


# ---------------------------------------------------------------------------
# Public API — delete (format-strict only)
# ---------------------------------------------------------------------------


def delete_payload(
    user_id: str,
    connector: str,
    *,
    actor: str,
    instance: str = "",
) -> bool:
    """Delete a row; emit audit on hit; return True iff a row was deleted.

    Format-strict only — admins/operators can clean up rows whose
    connector id has since been removed from the canonical
    :data:`connectors.registry.CONNECTORS` mapping. The audit record
    preserves a paper trail that the historical connector id existed
    and was cleaned up.
    """
    connectors_registry.validate_format(connector)
    _validate_instance(connector, instance, write=False)
    actor_clean = _actor_str(actor)
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: user_connector_credentials has no `scope` column
    # — encrypted per-user external-service secrets, not authored memory.
    row = ms.fetch_one(
        "DELETE FROM user_connector_credentials "
        "WHERE user_id = %s AND connector = %s AND instance = %s "
        "RETURNING key_version",
        (user_id, connector, instance),
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
        "connector_credentials: deleted user_id=%s connector=%s instance=%s actor=%s",
        user_id,
        connector,
        instance,
        actor_clean,
    )
    _fire_change(user_id, connector, action="delete", instance=instance)
    return True


# ---------------------------------------------------------------------------
# Public API — runtime resolve (registry-strict, env-fallback aware)
# ---------------------------------------------------------------------------


def resolve(
    user_id: str,
    connector: str,
    *,
    fallback_env: str | None = None,
    instance: str = "",
) -> dict[str, Any]:
    """Runtime entrypoint for downstream consumers (D3).

    Registry-strict. The env-fallback logic is delegated to
    :func:`services._credential_internals._resolve_env_fallback` so the
    runtime tier resolver and this per-user resolver share a single
    canonical implementation (no byte-for-byte mirror to drift).

    ``instance`` (LUM-170) selects a specific mailbox row of a
    multi-instance connector. The env-fallback is only consulted for the
    single-instance (``instance == ""``) path — a numbered mailbox has no
    env analogue.
    """
    connectors_registry.require_registered(connector)
    _validate_instance(connector, instance, write=False)

    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: user_connector_credentials has no `scope` column
    # — encrypted per-user external-service secrets, not authored memory.
    row = ms.fetch_one(
        "SELECT ciphertext FROM user_connector_credentials "
        "WHERE user_id = %s AND connector = %s AND instance = %s AND enabled",
        (user_id, connector, instance),
    )
    if row is not None:
        return _decrypt_payload(row["ciphertext"])

    # LUM-834 chunk B — gating the SELECT above is NOT gating this function.
    #
    # With `AND enabled`, a disabled row and an absent one both miss, and the
    # env fallback below returns the deployment's env credential whenever
    # AUTH_ENABLED is false — which is the default. So without this check,
    # switching a connector off would silently swap the stored credential for
    # the env one and the toggle would appear broken.
    #
    # Latent today: the only production caller passes user_id="" and can own
    # no row. Guarded anyway, because "latent" is a property of today's
    # callers and this function's own docstring calls it the runtime
    # entrypoint for downstream consumers.
    if is_disabled(user_id, connector, instance=instance):
        raise disabled_error(connector, instance=instance)

    if instance == "":
        env_payload = _resolve_env_fallback(connector, fallback_env)
        if env_payload is not None:
            return env_payload

    raise ConnectorNotConfigured(
        f"no credential row and no usable env fallback for "
        f"user_id={user_id!r} connector={connector!r} instance={instance!r}"
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
    # LUM-170: SELECT + UPDATE MUST carry ``instance`` — the PK is now
    # (user_id, connector, instance). Without it, a user with two imap
    # mailboxes would have both rows match the UPDATE WHERE and one
    # mailbox's ciphertext would silently overwrite the other's.
    rows = ms.fetch_all(
        "SELECT user_id, connector, instance, ciphertext, key_version "
        "FROM user_connector_credentials "
        "ORDER BY user_id ASC, connector ASC, instance ASC"
    )

    rotated = 0
    skipped = 0
    failed = 0

    for row in rows:
        user_id = row["user_id"]
        connector = row["connector"]
        instance = row.get("instance", "") or ""
        old_ciphertext = bytes(row["ciphertext"])

        if row["key_version"] == current_fp:
            skipped += 1
            continue

        try:
            new_token = mf.rotate(old_ciphertext)
        except Exception as exc:
            _log.error(
                "rotate failed for user_id=%s connector=%s (%s); ciphertext bytes NOT logged",
                user_id,
                connector,
                exc.__class__.__name__,
            )
            failed += 1
            continue

        try:
            mf.decrypt(new_token)
        except Exception as exc:
            _log.error(
                "rotate verify failed for user_id=%s connector=%s "
                "(%s); ciphertext bytes NOT logged",
                user_id,
                connector,
                exc.__class__.__name__,
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
                "WHERE user_id = %s AND connector = %s AND instance = %s",
                (new_token, current_fp, actor_clean, user_id, connector, instance),
            )
        except Exception as exc:
            _log.error(
                "rotate UPDATE failed for user_id=%s connector=%s (%s)",
                user_id,
                connector,
                exc.__class__.__name__,
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
        "connector_credentials rotation summary: rotated=%d skipped=%d failed=%d by_tier=%s",
        totals["rotated"],
        totals["skipped"],
        totals["failed"],
        by_tier,
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
    "ACTION_CRED_TESTED",
    "ACTION_CRED_ENABLED",
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
    "set_delivery_paused",
    "set_enabled",
    "is_disabled",
    "disabled_error",
    "count_rows_by_key_version",
    "reencrypt_all_to_current_version",
    "get_current_key_version",
    "reset_for_tests",
    "register_change_listener",
    "reset_listeners_for_tests",
]
