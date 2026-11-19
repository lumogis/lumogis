# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for ``services.graph_status`` (LUM-937)."""

from __future__ import annotations

import logging
from datetime import datetime
from datetime import timezone
from types import SimpleNamespace

import pytest
from models.capability import CapabilityLicenseMode
from models.capability import CapabilityManifest
from models.capability import CapabilityMaturity
from models.capability import CapabilityTransport
from services.capability_registry import RegisteredService

import config
from services import graph_status

GIB = 1024**3
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "configured,effective,registered,healthy,enough,expected",
    [
        ("inprocess", "inprocess", False, False, True, "unsupported_mode"),
        # The mode problem is named first, even on a small machine.
        ("inprocess", "disabled", False, False, False, "unsupported_mode"),
        ("service", "disabled", False, False, True, "service_wiring_failed"),
        # Memory is irrelevant once the graph is on.
        ("service", "service", True, True, False, "on"),
        ("service", "service", True, False, True, "service_unreachable"),
        ("service", "service", False, False, True, "service_unreachable"),
        ("disabled", "disabled", False, False, True, "off"),
        ("disabled", "disabled", False, False, None, "off"),
        ("disabled", "disabled", False, False, False, "off_low_memory"),
        # A stray registered graph service does not make it "on".
        ("disabled", "disabled", True, True, True, "off"),
    ],
)
def test_derive_state_truth_table(
    configured, effective, registered, healthy, enough, expected
) -> None:
    assert (
        graph_status.derive_state(
            configured_mode=configured,
            effective_mode=effective,
            service_registered=registered,
            service_healthy=healthy,
            memory_enough=enough,
            env_graph_mode=None,
            toggle=None,
            now=NOW,
        )
        == expected
    )


def _patch_psutil(monkeypatch, total) -> None:
    monkeypatch.setattr(graph_status.psutil, "virtual_memory", lambda: SimpleNamespace(total=total))


def test_total_memory_returns_psutil_total(monkeypatch) -> None:
    _patch_psutil(monkeypatch, 16 * GIB)
    assert graph_status.total_memory_bytes() == 16 * GIB


def test_total_memory_none_for_non_positive_total(monkeypatch) -> None:
    _patch_psutil(monkeypatch, 0)
    assert graph_status.total_memory_bytes() is None


def test_total_memory_none_when_psutil_fails(monkeypatch, caplog) -> None:
    def boom():
        raise OSError("no /proc/meminfo")

    monkeypatch.setattr(graph_status.psutil, "virtual_memory", boom)
    monkeypatch.setattr(graph_status, "_memory_read_failure_logged", False)
    with caplog.at_level(logging.DEBUG, logger=graph_status.__name__):
        assert graph_status.total_memory_bytes() is None
        assert graph_status.total_memory_bytes() is None
    records = [r for r in caplog.records if r.name == graph_status.__name__]
    assert len(records) == 1


def test_memory_enough_boundary() -> None:
    floor = int(10 * GIB * 0.95)
    assert graph_status.memory_enough(floor) is True
    assert graph_status.memory_enough(floor - 1) is False
    assert graph_status.memory_enough(None) is None
    # A nominal 10 GB VM reports about 9.6 GiB.
    assert graph_status.memory_enough(int(9.6 * GIB)) is True
    assert graph_status.memory_enough(int(7.6 * GIB)) is False


class _FakeRegistry:
    def __init__(self, svc) -> None:
        self._svc = svc
        self.asked: list[str] = []

    def get_service(self, service_id: str):
        self.asked.append(service_id)
        return self._svc


def real_graph_service(
    *, healthy: bool, seen: datetime | None = None, reason: str | None = None
) -> RegisteredService:
    """A real registry entry (not a stand-in), so a renamed field fails these tests."""
    manifest = CapabilityManifest(
        name="lumogis-graph",
        id="lumogis-graph",
        version="0.1.0",
        type="service",
        transport=CapabilityTransport.HTTP,
        license_mode=CapabilityLicenseMode.COMMUNITY,
        maturity=CapabilityMaturity.PREVIEW,
        description="Test graph service",
        tools=[],
        health_endpoint="/health",
        capabilities_endpoint="/capabilities",
        permissions_required=[],
        config_schema={"type": "object"},
        min_core_version="0.1.0",
        maintainer="t",
    )
    return RegisteredService(
        manifest=manifest,
        base_url="http://sentinel-base.invalid:8001",
        registered_at=datetime(2026, 11, 16, tzinfo=timezone.utc),
        healthy=healthy,
        last_seen_healthy=seen,
        last_unhealthy_reason=reason,
    )


def _modes(monkeypatch, mode: str) -> None:
    monkeypatch.setattr(config, "read_graph_mode_from_env", lambda: mode)
    monkeypatch.setattr(config, "get_graph_mode", lambda: mode)


def test_build_graph_status_service_on(monkeypatch) -> None:
    _modes(monkeypatch, "service")
    _patch_psutil(monkeypatch, 16 * GIB)
    seen = datetime(2026, 11, 16, 8, 0, tzinfo=timezone.utc)
    svc = real_graph_service(healthy=True, seen=seen)
    status = graph_status.build_graph_status(_FakeRegistry(svc))
    assert status.state == "on"
    assert status.service.registered is True
    assert status.service.healthy is True
    assert status.service.last_seen_healthy == seen


def test_build_graph_status_reason_passed_through_when_unhealthy(monkeypatch) -> None:
    _modes(monkeypatch, "service")
    _patch_psutil(monkeypatch, 16 * GIB)
    svc = real_graph_service(healthy=False, reason="timeout")
    status = graph_status.build_graph_status(_FakeRegistry(svc))
    assert status.state == "service_unreachable"
    assert status.service.healthy is False
    assert status.service.last_unhealthy_reason == "timeout"


def test_build_graph_status_service_absent(monkeypatch) -> None:
    _modes(monkeypatch, "disabled")
    _patch_psutil(monkeypatch, 16 * GIB)
    status = graph_status.build_graph_status(_FakeRegistry(None))
    assert status.service.registered is False
    assert status.service.healthy is None
    assert status.state == "off"


def test_build_graph_status_asks_registry_for_lumogis_graph_id(monkeypatch) -> None:
    _modes(monkeypatch, "disabled")
    _patch_psutil(monkeypatch, 16 * GIB)
    reg = _FakeRegistry(None)
    graph_status.build_graph_status(reg)
    assert reg.asked == ["lumogis-graph"]


# ---------------------------------------------------------------------------
# LUM-989 — starting / stopping / failed states and the button availability
# ---------------------------------------------------------------------------

from datetime import timedelta  # noqa: E402

from services.graph_toggle import ToggleRecord  # noqa: E402


def _state(
    *,
    configured="disabled",
    effective="disabled",
    registered=False,
    healthy=False,
    enough=True,
    env_mode=None,
    toggle=None,
    now=NOW,
    core_started_at=None,
):
    return graph_status.derive_state(
        configured_mode=configured,
        effective_mode=effective,
        service_registered=registered,
        service_healthy=healthy,
        memory_enough=enough,
        env_graph_mode=env_mode,
        toggle=toggle,
        now=now,
        core_started_at=core_started_at,
    )


def _rec(op, *, age=timedelta(minutes=1), failed_after=None, code=None):
    started = NOW - age
    return ToggleRecord(
        op=op,
        started_at=started,
        failed_at=(started + failed_after) if failed_after is not None else None,
        failed_code=code,
    )


def test_start_in_window_reads_starting_until_the_graph_is_healthy() -> None:
    rec = _rec("start")
    assert _state(env_mode="service", toggle=rec) == "starting"
    # Core was recreated with the graph on, registry discovery has not found it yet.
    assert (
        _state(configured="service", effective="service", env_mode="service", toggle=rec)
        == "starting"
    )
    # Healthy: on, whatever the record says.
    assert (
        _state(
            configured="service",
            effective="service",
            registered=True,
            healthy=True,
            env_mode="service",
            toggle=rec,
        )
        == "on"
    )


def test_starting_ends_with_its_window_and_falls_to_the_unchanged_rules() -> None:
    old = _rec("start", age=timedelta(minutes=36))
    assert (
        _state(configured="service", effective="service", env_mode="service", toggle=old)
        == "service_unreachable"
    )
    assert _state(env_mode="service", toggle=old) == "off"
    assert _state(env_mode="service", enough=False, toggle=old) == "off_low_memory"


def test_start_failed_carries_the_failure_and_on_beats_it() -> None:
    rec = _rec("start", failed_after=timedelta(seconds=20), code="compose_failed")
    assert _state(toggle=rec) == "start_failed"
    assert (
        _state(configured="service", effective="service", registered=True, healthy=True, toggle=rec)
        == "on"
    )


def test_a_failure_older_than_the_start_is_not_a_failure() -> None:
    rec = ToggleRecord(
        op="start",
        started_at=NOW - timedelta(minutes=1),
        failed_at=NOW - timedelta(minutes=30),
        failed_code="timeout",
    )
    assert _state(env_mode="service", toggle=rec) == "starting"


def test_stop_in_window_reads_stopping_until_core_is_recreated_then_off() -> None:
    rec = _rec("stop")  # started 1 minute before NOW
    # The old Core (started before the stop) still sees a healthy graph: stopping, not on.
    old_core = NOW - timedelta(days=1)
    assert (
        _state(
            configured="service",
            effective="service",
            registered=True,
            healthy=True,
            toggle=rec,
            core_started_at=old_core,
        )
        == "stopping"
    )
    # Core recreated after the stop began: the stop is over, long before the window.
    new_core = NOW - timedelta(seconds=10)
    assert _state(toggle=rec, core_started_at=new_core) == "off"


def test_a_stop_from_start_failed_reads_stopping_not_off() -> None:
    """Turn off after a failed start: Core never had the graph, yet the stop is running."""
    rec = _rec("stop", age=timedelta(seconds=5))
    state = _state(toggle=rec, env_mode="disabled", core_started_at=NOW - timedelta(days=1))
    assert state == "stopping"
    on, off = graph_status.availability(
        state,
        install_kind="release",
        has_graph=True,
        stack_reachable=True,
        env_writable=True,
        memory_enough=True,
    )
    assert (on.available, on.block) == (False, "in_progress")
    assert (off.available, off.block) == (False, "in_progress")


def test_stop_ends_with_its_window() -> None:
    old = _rec("stop", age=timedelta(minutes=11))
    assert (
        _state(configured="service", effective="service", registered=True, healthy=True, toggle=old)
        == "on"
    )


def test_stop_failed_beats_on() -> None:
    rec = _rec("stop", failed_after=timedelta(seconds=5), code="timeout")
    assert (
        _state(configured="service", effective="service", registered=True, healthy=True, toggle=rec)
        == "stop_failed"
    )


def test_unsupported_mode_beats_a_toggle_record() -> None:
    assert (
        _state(configured="inprocess", effective="inprocess", toggle=_rec("start"))
        == "unsupported_mode"
    )


def test_a_hand_set_service_mode_with_no_profile_is_unreachable_and_turn_off_cleans_it_up() -> None:
    state = _state(configured="service", effective="service", env_mode="service")
    assert state == "service_unreachable"
    on, off = graph_status.availability(
        state,
        install_kind="release",
        has_graph=True,
        stack_reachable=True,
        env_writable=True,
        memory_enough=True,
    )
    assert (on.available, on.block) == (False, "already_on")
    assert off.available is True


def _avail(state, **over):
    kw = dict(
        install_kind="release",
        has_graph=True,
        stack_reachable=True,
        env_writable=True,
        memory_enough=True,
    )
    kw.update(over)
    on, off = graph_status.availability(state, **kw)
    return (on.available, on.block, off.available, off.block)


@pytest.mark.parametrize(
    "state, expected",
    [
        ("off", (True, None, False, "already_off")),
        ("off_low_memory", (True, None, False, "already_off")),
        ("start_failed", (True, None, True, None)),
        ("stop_failed", (False, None, True, None)),
        ("starting", (False, "in_progress", False, "in_progress")),
        ("stopping", (False, "in_progress", False, "in_progress")),
        ("on", (False, "already_on", True, None)),
        ("service_unreachable", (False, "already_on", True, None)),
        ("service_wiring_failed", (False, "already_on", True, None)),
        ("unsupported_mode", (False, None, False, None)),
    ],
)
def test_availability_table(state, expected) -> None:
    assert _avail(state) == expected


@pytest.mark.parametrize(
    "over, block",
    [
        ({"install_kind": "checkout"}, "checkout"),
        ({"install_kind": "unknown"}, "checkout"),
        ({"has_graph": False}, "compose_too_old"),
        ({"stack_reachable": False}, "stack_control_unreachable"),
        ({"env_writable": False}, "env_unwritable"),
    ],
)
def test_install_blocks_disable_both_buttons(over, block) -> None:
    on, off = graph_status.availability(
        "start_failed",
        **{
            **dict(
                install_kind="release",
                has_graph=True,
                stack_reachable=True,
                env_writable=True,
                memory_enough=True,
            ),
            **over,
        },
    )
    assert (on.available, on.block) == (False, block)
    assert (off.available, off.block) == (False, block)


def test_low_memory_asks_for_confirmation_and_unknown_memory_does_not() -> None:
    on, _ = graph_status.availability(
        "off_low_memory",
        install_kind="release",
        has_graph=True,
        stack_reachable=True,
        env_writable=True,
        memory_enough=False,
    )
    assert on.available and on.needs_confirmation
    on, _ = graph_status.availability(
        "off",
        install_kind="release",
        has_graph=True,
        stack_reachable=True,
        env_writable=True,
        memory_enough=None,
    )
    assert on.available and not on.needs_confirmation
