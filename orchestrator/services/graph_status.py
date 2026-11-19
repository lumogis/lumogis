# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Read model for the admin Knowledge graph page (LUM-937).

Aggregates three things Core already knows, without probing or writing:
the configured and effective ``GRAPH_MODE`` (:mod:`config`), whether the
``lumogis-graph`` capability is registered and healthy (the
:class:`~services.capability_registry.CapabilityRegistry` that the lifespan and
scheduler already keep fresh), and how much memory this machine has (psutil).
:func:`derive_state` turns them into one server-computed state so the Web
client never re-derives it.
"""

from __future__ import annotations

import logging
from datetime import datetime
from datetime import timezone
from typing import TYPE_CHECKING
from typing import Final

import psutil
from models.api_v1 import AdminKnowledgeGraphMemory
from models.api_v1 import AdminKnowledgeGraphService
from models.api_v1 import AdminKnowledgeGraphStatus
from models.api_v1 import AdminKnowledgeGraphTurnOff
from models.api_v1 import AdminKnowledgeGraphTurnOn
from models.api_v1 import KnowledgeGraphState
from services.graph_toggle import ToggleRecord

import config
from services import graph_toggle
from services import host_env

if TYPE_CHECKING:
    from services.capability_registry import CapabilityRegistry

_log = logging.getLogger(__name__)

GRAPH_SERVICE_ID: Final = "lumogis-graph"

# Measured (LUM-991, ADR 271): on the LUM-929 seeded RC stack, FalkorDB plus the
# graph service peaked at 0.165 GiB together during a library Rescan (stopped after
# 50 of 294 files by decision). ADR 271's rule T = 8 GiB + ceil(peak, 0.5 GiB) + 1 GiB
# gives 9.5 GiB; rounded up to a whole 10 GiB so the copy and quickstart can say
# "about 10 GB" (test_quickstart_graph_memory.py keeps it a whole number of GiB).
# The quickstart and the admin copy quote it from here; test_quickstart_graph_memory.py
# pins the quickstart.
GRAPH_MIN_TOTAL_MEMORY_BYTES: Final = 10 * 1024**3

# The kernel reserves memory, so MemTotal reads below the nominal size (a
# "10 GB" VM reports about 9.6 GiB). Without this tolerance every exactly-10 GB
# machine would be told it is short.
MEMTOTAL_TOLERANCE: Final = 0.95

_memory_read_failure_logged = False


def total_memory_bytes() -> int | None:
    """Return this machine's total memory in bytes, or ``None`` if unreadable.

    Inside a container ``/proc/meminfo`` is the host's memory on Linux and the
    Docker VM's on Docker Desktop, which is the pool every Lumogis container
    shares. Core's own cgroup ``memory.max`` is deliberately **not** read: it is
    Core's limit, not the graph engine's (LUM-937 plan, self-review).
    """
    global _memory_read_failure_logged
    try:
        total = psutil.virtual_memory().total
    except Exception:
        if not _memory_read_failure_logged:
            _memory_read_failure_logged = True
            _log.debug("graph_status: could not read total memory", exc_info=True)
        return None
    if not isinstance(total, int) or isinstance(total, bool) or total <= 0:
        return None
    return total


def memory_enough(total: int | None) -> bool | None:
    """Whether ``total`` covers the graph's need; ``None`` when ``total`` is unknown."""
    if total is None:
        return None
    return total >= int(GRAPH_MIN_TOTAL_MEMORY_BYTES * MEMTOTAL_TOLERANCE)


def derive_state(
    *,
    configured_mode: str,
    effective_mode: str,
    service_registered: bool,
    service_healthy: bool,
    memory_enough: bool | None,
    env_graph_mode: str | None,
    toggle: ToggleRecord | None,
    now: datetime,
    core_started_at: datetime | None = None,
) -> KnowledgeGraphState:
    """Pure state derivation; first match wins (LUM-989 extends the LUM-937 order).

    1. ``unsupported_mode``. ``inprocess`` is reported as unsupported for every
       install: it works only on a Core build that includes ``services/lumogis-graph/``.
    2. A **stop** in progress or failed: ``stop_failed``, else ``stopping``. These
       come before ``on`` because the graph is still healthy while it is being
       stopped, and a failed stop leaves it running. ``stopping`` ends as soon as
       Core has been recreated (the sequence's last step): ``core_started_at`` is
       later than the stop's start. That holds whatever state Core was in when the
       stop began, including a graph that never came up (``start_failed``).
    3. ``on`` (effective ``service``, registered and healthy). A healthy graph
       always reads ``on`` whatever an older *start* failure says.
    4. A **start** failed or in progress: ``start_failed``, else ``starting``.
    5. ``service_wiring_failed``, ``service_unreachable``, ``off_low_memory``,
       ``off`` (the LUM-937 rules, unchanged).

    Nothing reads ``starting`` or ``stopping`` outside its window.
    """
    if configured_mode == "inprocess":
        return "unsupported_mode"
    toggle = toggle or ToggleRecord()
    if toggle.op == "stop":
        if toggle.failed():
            return "stop_failed"
        recreated = (
            core_started_at is not None
            and toggle.started_at is not None
            and core_started_at > toggle.started_at
        )
        if toggle.in_window(now) and not recreated:
            return "stopping"
    healthy_service = effective_mode == "service" and service_registered and service_healthy
    if healthy_service:
        return "on"
    if toggle.op == "start":
        if toggle.failed():
            return "start_failed"
        if toggle.in_window(now) and env_graph_mode != "disabled":
            return "starting"
    if configured_mode == "service" and effective_mode != "service":
        return "service_wiring_failed"
    if effective_mode == "service":
        return "service_unreachable"
    if memory_enough is False:
        return "off_low_memory"
    return "off"


# ``already_on``: the graph is configured on, whether or not it is healthy. A
# hand-set GRAPH_MODE=service with no profile reads service_unreachable; Turn off
# cleans it up, and the copy says to Turn off and on again.
_ALREADY_ON = frozenset({"on", "service_unreachable", "service_wiring_failed"})
_IN_PROGRESS = frozenset({"starting", "stopping"})
_ALREADY_OFF = frozenset({"off", "off_low_memory"})


def availability(
    state: KnowledgeGraphState,
    *,
    install_kind: str,
    has_graph: bool,
    stack_reachable: bool,
    env_writable: bool,
    memory_enough: bool | None,
) -> tuple[AdminKnowledgeGraphTurnOn, AdminKnowledgeGraphTurnOff]:
    """Which buttons the page offers. The Web client never derives this itself.

    | State | Turn on | Turn off |
    |---|---|---|
    | off, off_low_memory | yes | no |
    | start_failed | yes (Retry) | yes |
    | stop_failed | no | yes (Retry) |
    | starting, stopping | no (in_progress) | no (in_progress) |
    | on, service_unreachable, service_wiring_failed | no (already_on) | yes |

    On top of that, install blocks: a checkout (its graph image is built locally),
    a Release file without the ``graph`` profile, an unreachable sidecar, an
    unwritable ``.env``.
    """

    def install_block() -> str | None:
        if install_kind != "release":
            return "checkout"
        if not has_graph:
            return "compose_too_old"
        if not stack_reachable:
            return "stack_control_unreachable"
        if not env_writable:
            return "env_unwritable"
        return None

    block = install_block()

    # --- Turn on
    if state in _IN_PROGRESS:
        on = AdminKnowledgeGraphTurnOn(available=False, block="in_progress")
    elif state in _ALREADY_ON:
        on = AdminKnowledgeGraphTurnOn(available=False, block="already_on")
    elif state in ("unsupported_mode", "stop_failed"):
        on = AdminKnowledgeGraphTurnOn(available=False, block=None)
    elif block is not None:
        on = AdminKnowledgeGraphTurnOn(available=False, block=block)
    else:
        on = AdminKnowledgeGraphTurnOn(available=True, needs_confirmation=memory_enough is False)

    # --- Turn off
    if state in _IN_PROGRESS:
        off = AdminKnowledgeGraphTurnOff(available=False, block="in_progress")
    elif state in _ALREADY_OFF:
        off = AdminKnowledgeGraphTurnOff(available=False, block="already_off")
    elif state == "unsupported_mode":
        off = AdminKnowledgeGraphTurnOff(available=False, block=None)
    elif block is not None:
        off = AdminKnowledgeGraphTurnOff(available=False, block=block)
    else:
        off = AdminKnowledgeGraphTurnOff(available=True)
    return on, off


def build_graph_status(
    registry: CapabilityRegistry | None = None,
    *,
    now: datetime | None = None,
) -> AdminKnowledgeGraphStatus:
    """Build the admin knowledge-graph status. Never raises for a missing service."""
    if registry is None:
        registry = config.get_capability_registry()
    now = now or datetime.now(timezone.utc)
    configured_mode = config.read_graph_mode_from_env()
    effective_mode = config.get_graph_mode()
    svc = registry.get_service(GRAPH_SERVICE_ID)
    registered = svc is not None
    healthy = bool(svc.healthy) if registered else None
    total = total_memory_bytes()
    enough = memory_enough(total)
    toggle = graph_toggle.read_record()
    state = derive_state(
        configured_mode=configured_mode,
        effective_mode=effective_mode,
        service_registered=registered,
        service_healthy=bool(healthy),
        memory_enough=enough,
        env_graph_mode=host_env.read_key("GRAPH_MODE"),
        toggle=toggle,
        now=now,
        core_started_at=graph_toggle.CORE_STARTED_AT,
    )
    kind = graph_toggle.install_kind()
    release = kind == "release"
    turn_on, turn_off = availability(
        state,
        install_kind=kind,
        has_graph=release and graph_toggle.release_has_graph(),
        # The sidecar is only probed for a Release install, where it matters.
        stack_reachable=release and graph_toggle.stack_control_reachable(),
        env_writable=host_env.writable(),
        memory_enough=enough,
    )
    return AdminKnowledgeGraphStatus(
        state=state,
        mode=effective_mode,
        configured_mode=configured_mode,
        service=AdminKnowledgeGraphService(
            registered=registered,
            healthy=healthy,
            last_seen_healthy=svc.last_seen_healthy if registered else None,
            last_unhealthy_reason=svc.last_unhealthy_reason if registered else None,
        ),
        memory=AdminKnowledgeGraphMemory(
            total_bytes=total,
            required_bytes=GRAPH_MIN_TOTAL_MEMORY_BYTES,
            enough=enough,
        ),
        install_kind=kind,
        turn_on=turn_on,
        turn_off=turn_off,
        failed_code=toggle.failed_code if state in ("start_failed", "stop_failed") else None,
    )
