# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Which connectors are Ask/Do-settable (LUM-962, ADR 220).

One rule for every surface that writes ``connector_permissions.mode``. Do not
compare a connector against ``list_actions()`` anywhere else to decide this.

A connector's Ask/Do mode is settable when the Ask/Do gate enforces it:

* a registered **action** connector (``smtp``, ``imap``), or
* a registered **capability** service (``capability.{id}``).

Read-only tool connectors (``filesystem-mcp``, ``lumogis-memory``,
``lumogis-graph``) are not settable: ``check_permission`` only gates writes, so
DO would change nothing for them.

Every function reports "cannot tell" (the relevant registry raised) as ``None``
and never raises. Each caller owns its outage policy: ``PUT /me/permissions``
degrade-allows a plain connector (``Warning: 199``); Approvals fails closed.
Capability connectors fail closed on both. An outage in one registry never
affects the other kind.

Notes for maintainers:

* The WARN events below used to be emitted by ``routes.connector_permissions``;
  their **logger name** is now ``services.permission_connectors`` (message text
  unchanged).
* LUM-851 (list capability Ask/Do on ``/me/permissions``) must union
  :func:`known_action_connectors` and :func:`known_capability_connectors` from
  this module, reuse :func:`do_refused` if it ever refuses a DO flip, and extend
  the parity test ``test_put_permissions_and_approvals_set_mode_agree_on_connector_set``
  to ``GET /me/permissions``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from collections.abc import Iterable
from typing import NamedTuple

from services.capability_scopes import capability_connector
from services.capability_scopes import capability_id_from_connector
from services.capability_scopes import is_capability_connector

_log = logging.getLogger(__name__)

_ACTION_UNAVAILABLE = "action_registry_unavailable_at_permission_validation"
_CAPABILITY_UNAVAILABLE = "capability_registry_unavailable_at_permission_validation"


class ConnectorGate(NamedTuple):
    """Snapshot of one connector's Ask/Do standing, from a single registry read."""

    settable: bool
    # Sorted, distinct action types a DO flip would un-gate; ``()`` when not settable.
    gated_action_types: tuple[str, ...]


def known_action_connectors() -> set[str] | None:
    """Distinct connector names from the live **action** registry (``list_actions``).

    Returns ``None`` on action-registry failure so the caller can degrade-allow
    first-party connectors with a ``Warning: 199`` advisory header (OQ #4).
    """
    try:
        from actions.registry import list_actions

        return {spec["connector"] for spec in list_actions() if spec.get("connector")}
    except Exception:  # noqa: BLE001 — caller owns the outage policy
        _log.warning(_ACTION_UNAVAILABLE)
        return None


def known_capability_connectors() -> set[str] | None:
    """`capability.{id}` names for every registered capability service (LUM-612).

    Sourced in a **separate** try from the action registry so a capability
    -registry error never weakens plain-connector validation. Returns ``None`` on
    failure — callers **fail closed** for `capability.*` writes (never degrade
    -allow an unverified capability grant). The set is legitimately empty until
    first discovery completes at startup, so real capabilities 404 briefly then.
    """
    try:
        import config

        reg = config.get_capability_registry()
        return {capability_connector(svc.manifest.id) for svc in reg.all_services()}
    except Exception:  # noqa: BLE001
        _log.warning(_CAPABILITY_UNAVAILABLE)
        return None


def registered_action_pairs() -> set[tuple[str, str]] | None:
    """``{(connector, action_type)}`` for every registered action; ``None`` on failure."""
    try:
        from actions.registry import list_actions

        return {
            (spec["connector"], spec["action_type"])
            for spec in list_actions()
            if spec.get("connector") and spec.get("action_type")
        }
    except Exception:  # noqa: BLE001
        _log.warning(_ACTION_UNAVAILABLE)
        return None


def connector_gate(connector: str) -> ConnectorGate | None:
    """One registry read, one snapshot, for everything Approvals needs about ``connector``.

    ``None`` means "cannot tell" (the relevant registry raised); every caller
    must treat it as **refuse**. Never consults the registry of the other kind.

    * ``capability.{id}``: settable iff the service is registered; its gated
      action types are its write-tool names (``action_type == tool.name``).
    * anything else: settable iff some registered action names it as connector;
      its gated action types are those actions' ``action_type`` values (every
      registered action counts as gated).
    """
    if is_capability_connector(connector):
        try:
            import config

            svc = config.get_capability_registry().get_service(
                capability_id_from_connector(connector)
            )
            if svc is None:
                return ConnectorGate(False, ())
            return ConnectorGate(
                True,
                tuple(sorted({t.name for t in svc.manifest.tools if t.is_write})),
            )
        except Exception:  # noqa: BLE001
            _log.warning(_CAPABILITY_UNAVAILABLE)
            return None

    try:
        from actions.registry import list_actions

        specs = [s for s in list_actions() if s.get("connector") == connector]
        if not specs:
            return ConnectorGate(False, ())
        return ConnectorGate(
            True,
            tuple(sorted({s["action_type"] for s in specs if s.get("action_type")})),
        )
    except Exception:  # noqa: BLE001
        _log.warning(_ACTION_UNAVAILABLE)
        return None


def do_refused(
    gated_action_types: Iterable[str],
    *,
    is_hard_limited: Callable[[str], bool],
) -> bool:
    """True when a DO flip must be refused: **any** gated action type is hard-limited.

    ``check_permission`` skips the hard-limit test under DO, so a connector in DO
    runs any hard-limited type it has without approval. Pure; the predicate is
    injected so callers pass the one their tests already patch.
    """
    return any(is_hard_limited(t) for t in gated_action_types)
