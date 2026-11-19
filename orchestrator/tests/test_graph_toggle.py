# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``services/graph_toggle.py`` — Turn on / Turn off decisions and records (LUM-989)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import config
from services import graph_status
from services import graph_toggle
from services import host_env

GIB = 1024**3
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
SECRET_SENTINEL = "SENTINEL-graph-webhook-secret-0123456789abcdef0123456789abcdef"
RELEASE_HEADER = "# lumogis-compose: 0123456789abcdef\n"


class FakeSettingsStore:
    """Just enough of ``app_settings`` for the toggle record."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def execute(self, query, params=None):
        if "INSERT INTO app_settings" in query:
            self.data[params[0]] = params[1]

    def fetch_all(self, query, params=None):
        if "app_settings" in query:
            # VERIFY-PLAN: updated to match implementation (one `key = ANY(%s)` list param)
            wanted = set(params[0]) if params else set()
            return [{"key": k, "value": v} for k, v in self.data.items() if k in wanted]
        return []

    def fetch_one(self, query, params=None):
        return None


class FakeRegistry:
    def __init__(self, svc=None) -> None:
        self.svc = svc

    def get_service(self, _id):
        return self.svc


class Install:
    """A release-shaped install in a temp folder, with its collaborators patched."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.monkeypatch = monkeypatch
        self.env = tmp_path / ".env"
        self.compose = tmp_path / "docker-compose.yml"
        self.store = FakeSettingsStore()
        self.audits: list = []
        self.compose.write_text(
            RELEASE_HEADER
            + "services:\n  lumogis-graph:\n    image: x\n    profiles:\n    - graph\n"
            "  falkordb:\n    image: y\n    profiles:\n    - graph\n",
            encoding="utf-8",
        )
        self.env.write_text("A=1\nGRAPH_MODE=disabled\n", encoding="utf-8")
        monkeypatch.setattr(host_env, "ENV_PATH", self.env)
        monkeypatch.setattr(graph_toggle, "COMPOSE_PATH", self.compose)
        graph_toggle._reset_caches_for_tests()
        monkeypatch.setattr(graph_toggle, "stack_control_reachable", lambda: True)
        monkeypatch.setitem(config._instances, "metadata_store", self.store)
        monkeypatch.setitem(config._instances, "capability_registry", FakeRegistry())
        monkeypatch.setattr(graph_status, "total_memory_bytes", lambda: 16 * GIB)
        self.modes(env="disabled", effective="disabled")
        monkeypatch.setattr(
            "actions.audit.write_audit", lambda entry, reverse_token=None: self.audits.append(entry)
        )

    def modes(self, *, env: str, effective: str) -> None:
        self.monkeypatch.setattr(config, "read_graph_mode_from_env", lambda: env)
        self.monkeypatch.setattr(config, "get_graph_mode", lambda: effective)

    def memory(self, total) -> None:
        self.monkeypatch.setattr(graph_status, "total_memory_bytes", lambda: total)

    def env_text(self) -> str:
        return self.env.read_text(encoding="utf-8")


@pytest.fixture
def install(tmp_path, monkeypatch) -> Install:
    return Install(tmp_path, monkeypatch)


# --------------------------------------------------------------- install kind


def test_install_kind_release_checkout_unknown(tmp_path):
    rel = tmp_path / "rel.yml"
    rel.write_text(RELEASE_HEADER + "services: {}\n")
    chk = tmp_path / "chk.yml"
    chk.write_text("services: {}\n")
    assert graph_toggle.install_kind(rel) == "release"
    assert graph_toggle.install_kind(chk) == "checkout"
    assert graph_toggle.install_kind(tmp_path / "absent.yml") == "unknown"


def test_release_has_graph_true_false_and_errors(tmp_path):
    graph_toggle._reset_caches_for_tests()
    with_graph = tmp_path / "a.yml"
    with_graph.write_text(
        RELEASE_HEADER + "services:\n  lumogis-graph:\n    profiles: [graph]\n    image: x\n"
    )
    without = tmp_path / "b.yml"
    without.write_text(RELEASE_HEADER + "services:\n  core:\n    image: x\n")
    other_profile = tmp_path / "c.yml"
    other_profile.write_text("services:\n  lumogis-graph:\n    profiles: [debug]\n    image: x\n")
    broken = tmp_path / "d.yml"
    broken.write_text("services: [unclosed\n")
    assert graph_toggle.release_has_graph(with_graph) is True
    assert graph_toggle.release_has_graph(without) is False
    assert graph_toggle.release_has_graph(other_profile) is False
    assert graph_toggle.release_has_graph(broken) is False
    assert graph_toggle.release_has_graph(tmp_path / "absent.yml") is False


def test_a_stale_fingerprint_with_the_profile_present_is_still_allowed(install):
    # The header's fingerprint (0123…) matches no image: compose_freshness would say stale.
    graph_toggle.enable(confirm_low_memory=False, actor="admin-1", now=NOW)
    assert "GRAPH_MODE=service" in install.env_text()


# -------------------------------------------------------------------- enable


def test_enable_writes_exactly_the_documented_keys(install):
    install.env.write_text(
        "A=1\nCOMPOSE_PROFILES=librechat\nCAPABILITY_SERVICE_URLS=http://other:1\n"
    )
    graph_toggle.enable(confirm_low_memory=False, actor="admin-1", now=NOW)
    text = install.env_text()
    assert "COMPOSE_PROFILES=librechat,graph\n" in text
    assert "GRAPH_MODE=service\n" in text
    assert "KG_SERVICE_URL=http://lumogis-graph:8001\n" in text
    assert "CAPABILITY_SERVICE_URLS=http://other:1,http://lumogis-graph:8001\n" in text
    assert "FALKORDB_URL=redis://falkordb:6379\n" in text
    assert re.search(r"^GRAPH_WEBHOOK_SECRET=[0-9a-f]{64}$", text, re.MULTILINE)
    assert text.startswith("A=1\n")
    keys = {ln.split("=", 1)[0] for ln in text.splitlines() if "=" in ln}
    assert keys == {
        "A",
        "COMPOSE_PROFILES",
        "CAPABILITY_SERVICE_URLS",
        "GRAPH_MODE",
        "KG_SERVICE_URL",
        "FALKORDB_URL",
        "GRAPH_WEBHOOK_SECRET",
    }


def test_enable_keeps_a_real_secret(install):
    install.env.write_text(f"GRAPH_WEBHOOK_SECRET={SECRET_SENTINEL}\n")
    graph_toggle.enable(confirm_low_memory=False, actor="a", now=NOW)
    assert install.env_text().count(SECRET_SENTINEL) == 1


@pytest.mark.parametrize("placeholder", sorted(host_env.PLACEHOLDERS - {""}) + ["EMPTY"])
def test_enable_replaces_each_placeholder_with_a_64_hex_secret(install, placeholder):
    value = "" if placeholder == "EMPTY" else placeholder
    install.env.write_text(f"GRAPH_WEBHOOK_SECRET={value}\n")
    graph_toggle.enable(confirm_low_memory=False, actor="a", now=NOW)
    assert re.search(r"^GRAPH_WEBHOOK_SECRET=[0-9a-f]{64}$", install.env_text(), re.MULTILINE)


def test_enable_twice_after_a_failure_does_not_duplicate_lines(install):
    graph_toggle.enable(confirm_low_memory=False, actor="a", now=NOW)
    graph_toggle._save(
        {
            graph_toggle.KEY_FAILED_AT: (NOW + timedelta(seconds=5)).isoformat(),
            graph_toggle.KEY_FAILED_CODE: "compose_failed",
        }
    )
    graph_toggle.enable(confirm_low_memory=False, actor="a", now=NOW + timedelta(seconds=30))
    text = install.env_text()
    for key in (
        "COMPOSE_PROFILES",
        "GRAPH_MODE",
        "KG_SERVICE_URL",
        "FALKORDB_URL",
        "GRAPH_WEBHOOK_SECRET",
    ):
        assert len(re.findall(rf"^{key}=", text, re.MULTILINE)) == 1, key


def test_low_memory_without_confirmation_needs_it_and_writes_nothing(install):
    install.memory(4 * GIB)
    before = install.env_text()
    with pytest.raises(graph_toggle.NeedsConfirmation):
        graph_toggle.enable(confirm_low_memory=False, actor="a", now=NOW)
    assert install.env_text() == before and install.store.data == {}


def test_low_memory_with_confirmation_and_unknown_memory_write(install):
    install.memory(4 * GIB)
    graph_toggle.enable(confirm_low_memory=True, actor="a", now=NOW)
    assert "GRAPH_MODE=service" in install.env_text()


def test_unknown_memory_is_allowed(install):
    install.memory(None)
    graph_toggle.enable(confirm_low_memory=False, actor="a", now=NOW)
    assert "GRAPH_MODE=service" in install.env_text()


@pytest.mark.parametrize(
    "setup, block",
    [
        (lambda i: i.compose.write_text("services: {}\n"), "checkout"),
        (lambda i: i.compose.unlink(), "checkout"),
        (
            lambda i: i.compose.write_text(RELEASE_HEADER + "services:\n  core:\n    image: x\n"),
            "compose_too_old",
        ),
        (
            lambda i: i.monkeypatch.setattr(graph_toggle, "stack_control_reachable", lambda: False),
            "stack_control_unreachable",
        ),
        (lambda i: i.env.unlink(), "env_unwritable"),
    ],
)
def test_each_install_block_blocks_and_writes_nothing(install, setup, block):
    setup(install)
    snapshot = install.env.read_text() if install.env.exists() else None
    with pytest.raises(graph_toggle.Blocked) as exc:
        graph_toggle.enable(confirm_low_memory=True, actor="a", now=NOW)
    assert exc.value.code == block
    assert install.store.data == {}
    assert (install.env.read_text() if install.env.exists() else None) == snapshot


def test_enable_when_already_on_is_blocked(install):
    install.modes(env="service", effective="service")
    with pytest.raises(graph_toggle.Blocked) as exc:
        graph_toggle.enable(confirm_low_memory=False, actor="a", now=NOW)
    assert exc.value.code == "already_on"


def test_a_second_enable_inside_the_window_is_busy_with_no_write(install):
    graph_toggle.enable(confirm_low_memory=False, actor="a", now=NOW)
    after_first = install.env_text()
    store_after_first = dict(install.store.data)
    with pytest.raises(graph_toggle.Busy):
        graph_toggle.enable(confirm_low_memory=False, actor="b", now=NOW + timedelta(minutes=2))
    assert install.env_text() == after_first
    assert install.store.data == store_after_first
    assert len(install.audits) == 1


def test_disable_while_a_start_is_in_flight_is_busy(install):
    graph_toggle.enable(confirm_low_memory=False, actor="a", now=NOW)
    with pytest.raises(graph_toggle.Busy):
        graph_toggle.disable(actor="a", now=NOW + timedelta(minutes=1))


def test_enable_records_the_start(install):
    graph_toggle.enable(confirm_low_memory=False, actor="a", now=NOW)
    rec = graph_toggle.read_record()
    assert rec.op == "start" and rec.started_at == NOW and rec.failed_at is None


# ------------------------------------------------------------------- disable


def _on(install):
    install.env.write_text(
        "A=1\nCOMPOSE_PROFILES=librechat,graph\nGRAPH_MODE=service\nKG_SERVICE_URL=http://lumogis-graph:8001\n"
        f"CAPABILITY_SERVICE_URLS=http://other:1,http://lumogis-graph:8001\nFALKORDB_URL=redis://falkordb:6379\n"
        f"GRAPH_WEBHOOK_SECRET={SECRET_SENTINEL}\n"
    )
    svc = SimpleNamespace(healthy=True, last_seen_healthy=None, last_unhealthy_reason=None)
    install.monkeypatch.setitem(config._instances, "capability_registry", FakeRegistry(svc))
    install.modes(env="service", effective="service")


def test_disable_writes_disabled_removes_the_profile_and_url_and_keeps_the_secret(install):
    _on(install)
    graph_toggle.disable(actor="a", now=NOW)
    text = install.env_text()
    assert "GRAPH_MODE=disabled\n" in text and "GRAPH_MODE=service" not in text
    assert "COMPOSE_PROFILES=librechat\n" in text
    assert "CAPABILITY_SERVICE_URLS=http://other:1\n" in text
    assert f"GRAPH_WEBHOOK_SECRET={SECRET_SENTINEL}\n" in text
    assert "FALKORDB_URL=redis://falkordb:6379\n" in text  # FalkorDB's data and URL stay
    rec = graph_toggle.read_record()
    assert rec.op == "stop" and rec.started_at == NOW


def test_disable_is_allowed_after_a_start_failure(install):
    graph_toggle.enable(confirm_low_memory=False, actor="a", now=NOW)
    graph_toggle._save(
        {
            graph_toggle.KEY_FAILED_AT: (NOW + timedelta(seconds=9)).isoformat(),
            graph_toggle.KEY_FAILED_CODE: "compose_failed",
        }
    )
    graph_toggle.disable(actor="a", now=NOW + timedelta(minutes=1))
    assert "GRAPH_MODE=disabled" in install.env_text()


def test_disable_on_a_hand_set_service_mode_cleans_it_up(install):
    install.env.write_text("GRAPH_MODE=service\n")
    install.modes(env="service", effective="service")  # no registry entry, no profile
    graph_toggle.disable(actor="a", now=NOW)
    assert install.env_text() == "GRAPH_MODE=disabled\n"


def test_disable_when_off_is_blocked(install):
    with pytest.raises(graph_toggle.Blocked) as exc:
        graph_toggle.disable(actor="a", now=NOW)
    assert exc.value.code == "already_off"


# -------------------------------------------------------------------- audit


def test_audit_rows_carry_confirm_flag_and_install_kind_and_no_secret(install):
    install.env.write_text(f"GRAPH_WEBHOOK_SECRET={SECRET_SENTINEL}\n")
    install.memory(4 * GIB)
    graph_toggle.enable(confirm_low_memory=True, actor="admin-7", now=NOW)
    (entry,) = install.audits
    assert entry.action_name == "knowledge_graph.turned_on"
    assert entry.connector == "admin" and entry.mode == "system" and entry.user_id == "admin-7"
    assert json.loads(entry.input_summary) == {
        "confirm_low_memory": True,
        "install_kind": "release",
    }
    assert SECRET_SENTINEL not in entry.input_summary + entry.result_summary


# ------------------------------------------------------------ background call


class FakeResponse:
    def __init__(self, status=200, body=None, bad_json=False):
        self.status_code = status
        self._body = body
        self._bad = bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._body


class FakeClock:
    """Monotonic clock that only moves when run() sleeps (no real waiting)."""

    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def mono(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


def _run_with(install, monkeypatch, outcome, op="start", clock=None):
    """``outcome`` is one response/exception for every call, or a list consumed in order."""
    calls = []
    outcomes = list(outcome) if isinstance(outcome, list) else None
    clock = clock or FakeClock()

    def post(path, *, json=None, timeout):
        calls.append((path, timeout))
        this = outcomes.pop(0) if outcomes is not None else outcome
        if isinstance(this, BaseException):
            raise this
        return this

    monkeypatch.setattr(graph_toggle.stack_control, "post", post)
    graph_toggle.enable(confirm_low_memory=False, actor="a", now=NOW) if op == "start" else None
    graph_toggle.run(
        op, now=lambda: NOW + timedelta(seconds=30), _sleep=clock.sleep, _mono=clock.mono
    )
    return calls, graph_toggle.read_record()


def test_unreachable_right_after_a_sidecar_restart_is_retried_until_it_answers(
    install, monkeypatch
):
    # LUM-1001: stack-control recreated a few seconds ago, not listening yet.
    outcomes = [
        httpx.ConnectError("refused"),
        httpx.ConnectError("refused"),
        FakeResponse(200, {"ok": True, "code": "started"}),
    ]
    clock = FakeClock()
    calls, rec = _run_with(install, monkeypatch, outcomes, clock=clock)
    assert len(calls) == 3
    assert clock.sleeps == [graph_toggle.UNREACHABLE_RETRY_INTERVAL_SEC] * 2
    assert rec.failed_at is None and rec.failed_code is None


def test_unreachable_is_recorded_once_the_retry_budget_is_spent(install, monkeypatch):
    clock = FakeClock()
    calls, rec = _run_with(install, monkeypatch, httpx.ConnectError("refused"), clock=clock)
    assert rec.failed_code == "unreachable"
    assert clock.t <= graph_toggle.UNREACHABLE_RETRY_SEC
    assert (
        len(calls)
        == int(graph_toggle.UNREACHABLE_RETRY_SEC // graph_toggle.UNREACHABLE_RETRY_INTERVAL_SEC)
        + 1
    )


def test_errors_after_the_request_was_sent_are_never_retried(install, monkeypatch):
    calls, rec = _run_with(install, monkeypatch, httpx.ReadError("reset"))
    assert len(calls) == 1 and rec.failed_at is None


def test_run_start_posts_the_fixed_path_with_the_long_timeout(install, monkeypatch):
    calls, rec = _run_with(install, monkeypatch, FakeResponse(200, {"ok": True, "code": "started"}))
    assert [c[0] for c in calls] == ["/graph/start"]
    timeout = calls[0][1]
    assert timeout.read == 1800.0 and timeout.connect == 5.0
    assert rec.failed_at is None


def test_connect_error_records_unreachable(install, monkeypatch):
    _, rec = _run_with(install, monkeypatch, httpx.ConnectError("refused"))
    assert rec.failed() and rec.failed_code == "unreachable"


def test_connect_timeout_records_unreachable(install, monkeypatch):
    _, rec = _run_with(install, monkeypatch, httpx.ConnectTimeout("slow"))
    assert rec.failed_code == "unreachable"


def test_read_error_after_send_records_nothing_because_core_was_recreated(install, monkeypatch):
    _, rec = _run_with(install, monkeypatch, httpx.ReadError("connection reset"))
    assert rec.failed_at is None and rec.failed_code is None


def test_remote_protocol_error_records_nothing(install, monkeypatch):
    _, rec = _run_with(install, monkeypatch, httpx.RemoteProtocolError("server disconnected"))
    assert rec.failed_at is None


@pytest.mark.parametrize("code", ["compose_failed", "timeout"])
def test_ok_false_records_the_code(install, monkeypatch, code):
    _, rec = _run_with(install, monkeypatch, FakeResponse(200, {"ok": False, "code": code}))
    assert rec.failed() and rec.failed_code == code


def test_busy_is_never_recorded_as_a_failure(install, monkeypatch):
    _, rec = _run_with(install, monkeypatch, FakeResponse(200, {"ok": False, "code": "busy"}))
    assert rec.failed_at is None and rec.failed_code is None


def test_an_unexpected_http_status_or_body_records_compose_failed(install, monkeypatch):
    _, rec = _run_with(install, monkeypatch, FakeResponse(500, None))
    assert rec.failed_code == "compose_failed"
    _, rec = _run_with(install, monkeypatch, FakeResponse(200, None, bad_json=True))
    assert rec.failed_code == "compose_failed"


def test_an_unknown_code_is_recorded_as_compose_failed(install, monkeypatch):
    _, rec = _run_with(
        install, monkeypatch, FakeResponse(200, {"ok": False, "code": "something-new"})
    )
    assert rec.failed_code == "compose_failed"


def test_run_stop_uses_the_stop_path(install, monkeypatch):
    _on(install)
    graph_toggle.disable(actor="a", now=NOW)
    paths = []
    monkeypatch.setattr(
        graph_toggle.stack_control,
        "post",
        lambda path, *, json=None, timeout: paths.append(path) or FakeResponse(200, {"ok": True}),
    )
    graph_toggle.run("stop")
    assert paths == ["/graph/stop"]


# ----------------------------------------------------------- no secret leakage


def test_the_secret_never_reaches_a_log_record(install, monkeypatch, caplog):
    install.env.write_text(f"GRAPH_WEBHOOK_SECRET={SECRET_SENTINEL}\n")
    monkeypatch.setattr(
        graph_toggle.stack_control,
        "post",
        lambda path, *, json=None, timeout: FakeResponse(
            200, {"ok": False, "code": "compose_failed"}
        ),
    )
    with caplog.at_level(logging.DEBUG):
        graph_toggle.enable(confirm_low_memory=False, actor="a", now=NOW)
        graph_toggle.run("start")
        _on(install)
        install.store.data.clear()
        graph_toggle.disable(actor="a", now=NOW + timedelta(hours=1))
    assert SECRET_SENTINEL not in caplog.text
    assert SECRET_SENTINEL not in json.dumps(install.store.data)


# ----------------------------------------------------------- the status probe


def test_the_sidecar_probe_is_cached_for_30_seconds(monkeypatch):
    graph_toggle._reset_caches_for_tests()
    calls = []
    monkeypatch.setattr(
        graph_toggle.stack_control,
        "get",
        lambda path, *, timeout: calls.append((path, timeout)) or SimpleNamespace(status_code=200),
    )
    t = [100.0]
    clock = lambda: t[0]  # noqa: E731
    assert graph_toggle.stack_control_reachable(_mono=clock) is True
    t[0] = 120.0
    assert graph_toggle.stack_control_reachable(_mono=clock) is True
    assert calls == [("/health", 1.0)]
    t[0] = 131.0
    graph_toggle.stack_control_reachable(_mono=clock)
    assert len(calls) == 2


def test_an_unreachable_sidecar_reads_false(monkeypatch):
    graph_toggle._reset_caches_for_tests()

    def boom(path, *, timeout):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(graph_toggle.stack_control, "get", boom)
    assert graph_toggle.stack_control_reachable() is False
    graph_toggle._reset_caches_for_tests()


def test_a_non_object_body_is_recorded_as_compose_failed(install, monkeypatch):
    _, rec = _run_with(install, monkeypatch, FakeResponse(200, ["ok"]))
    assert rec.failed_code == "compose_failed"


def test_the_windows_outlast_the_sidecars_budget():
    # stack-control: pull 1500 s + recreate 120 s; Core then waits up to 5 minutes for discovery.
    assert graph_toggle.START_WINDOW >= timedelta(seconds=1500 + 120 + 300)
    assert graph_toggle.STOP_WINDOW >= timedelta(seconds=120 + 60 + 120)


def test_a_failed_env_write_restores_the_previous_record(install, monkeypatch):
    graph_toggle.enable(confirm_low_memory=False, actor="a", now=NOW)
    graph_toggle._save(
        {
            graph_toggle.KEY_FAILED_AT: (NOW + timedelta(seconds=9)).isoformat(),
            graph_toggle.KEY_FAILED_CODE: "timeout",
        }
    )
    before = dict(install.store.data)

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(host_env, "update", boom)
    with pytest.raises(OSError):
        graph_toggle.disable(actor="a", now=NOW + timedelta(minutes=1))
    assert install.store.data == before  # the stop was never recorded


def test_a_record_failure_leaves_env_untouched(install, monkeypatch):
    before = install.env_text()

    def boom(*_a, **_k):
        raise RuntimeError("postgres down")

    monkeypatch.setattr(graph_toggle, "_save", boom)
    with pytest.raises(RuntimeError):
        graph_toggle.enable(confirm_low_memory=False, actor="a", now=NOW)
    assert install.env_text() == before


def test_graph_toggle_keeps_stt_profile(install):
    """LUM-995: the Release turns speech-to-text on with ``COMPOSE_PROFILES=stt``; the graph
    toggle merges into that line and never drops it."""
    install.env.write_text("A=1\nCOMPOSE_PROFILES=stt\nSTT_BACKEND=whisper_sidecar\n")
    graph_toggle.enable(confirm_low_memory=False, actor="admin-1", now=NOW)
    assert "COMPOSE_PROFILES=stt,graph\n" in install.env_text()
    assert install.env_text().count("COMPOSE_PROFILES=") == 1
    install.monkeypatch.setitem(
        config._instances,
        "capability_registry",
        FakeRegistry(
            SimpleNamespace(healthy=True, last_seen_healthy=None, last_unhealthy_reason=None)
        ),
    )
    install.modes(env="service", effective="service")
    graph_toggle.disable(actor="a", now=NOW)
    assert "COMPOSE_PROFILES=stt\n" in install.env_text()
