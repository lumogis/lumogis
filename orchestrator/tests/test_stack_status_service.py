# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for :mod:`services.stack_status`."""

from __future__ import annotations

import time
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from models.api_v1 import AdminDiagnosticsStoreItem
from models.api_v1 import StackStatusServiceItem
from models.api_v1 import StackStatusStorageItem
from services import stack_status as svc


@pytest.fixture(autouse=True)
def reset_stack_status_cache() -> None:
    with svc._cache_lock:
        svc._cache_payload = None
        svc._cache_at = None
        svc._fetch_in_progress = False
    yield
    with svc._cache_lock:
        svc._cache_payload = None
        svc._cache_at = None
        svc._fetch_in_progress = False


def test_stack_status_maps_compose_running_to_healthy() -> None:
    row = {"State": "running", "Health": "healthy"}
    state, _, _ = svc._merge_compose_and_ping(row, None)
    assert state == "healthy"


def test_stack_status_compose_exited_maps_down() -> None:
    row = {"State": "exited"}
    state, _, _ = svc._merge_compose_and_ping(row, None)
    assert state == "down"


def test_stack_status_runtime_detail_allowlist() -> None:
    row = {
        "State": "running",
        "Health": "healthy",
        "Image": "postgres:16",
        "Publishers": [{"URL": "0.0.0.0:5432"}],
        "Command": "docker-entrypoint.sh",
        "RestartCount": 2,
    }
    detail = svc._sanitize_runtime_detail(row)
    assert set(detail.keys()) <= {"compose_state", "health", "restart_count"}
    assert detail["compose_state"] == "running"
    assert detail["health"] == "healthy"
    assert detail["restart_count"] == 2
    assert "Image" not in detail


def test_stack_status_compose_ps_json_array() -> None:
    compose_ps = [{"Service": "postgres", "State": "running", "Health": "healthy"}]
    by_id = svc._index_compose_rows(compose_ps)
    assert "postgres" in by_id


def test_stack_status_storage_warn_at_threshold() -> None:
    assert svc._storage_status(81.0) == "warn"


def test_stack_status_storage_critical_at_threshold() -> None:
    assert svc._storage_status(96.0) == "critical"


def test_stack_status_critical_storage_degrades_overall() -> None:
    services = [
        StackStatusServiceItem(
            id="postgres",
            display_name="Postgres",
            state="healthy",
        ),
        StackStatusServiceItem(
            id="orchestrator",
            display_name="Orchestrator",
            state="healthy",
        ),
        StackStatusServiceItem(
            id="qdrant",
            display_name="Qdrant",
            state="healthy",
        ),
    ]
    storage = [
        StackStatusStorageItem(
            mount_id="host_root",
            path_label="root",
            used_percent=96.0,
            status="critical",
        )
    ]
    assert svc._compute_overall(services, storage) == "degraded"


def test_stack_status_postgres_down_returns_down_overall() -> None:
    services = [
        StackStatusServiceItem(
            id="postgres",
            display_name="Postgres",
            state="down",
        ),
        StackStatusServiceItem(
            id="orchestrator",
            display_name="Orchestrator",
            state="healthy",
        ),
        StackStatusServiceItem(
            id="qdrant",
            display_name="Qdrant",
            state="healthy",
        ),
    ]
    assert svc._compute_overall(services, []) == "down"


def test_stack_status_ping_unreachable_running_is_degraded() -> None:
    row = {"State": "running", "Health": "healthy"}
    ping = AdminDiagnosticsStoreItem(name="postgres", status="unreachable", message=None)
    state, _, _ = svc._merge_compose_and_ping(row, ping)
    assert state == "degraded"


def test_stack_status_cache_skips_second_df_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"compose_ps": [], "system_df": [], "fetched_at": "2026-01-01T00:00:00Z"}
    calls: list[int] = []

    def fake_fetch() -> tuple[dict, bool]:
        calls.append(1)
        return payload, True

    monkeypatch.setattr(svc, "_fetch_stack_control_status_unlocked", fake_fetch)
    monkeypatch.setattr(svc, "_restart_secret", lambda: "secret")
    monkeypatch.setattr(svc, "_build_store_pings", lambda: {})
    monkeypatch.setattr(svc, "_local_storage_rows", lambda _df: [])
    monkeypatch.setattr(svc, "_ollama_read_only_block", lambda: ([], []))

    svc.build_stack_status_response()
    svc.build_stack_status_response()
    assert len(calls) == 1


def test_stack_status_http_timeout_exceeds_df_timeout() -> None:
    assert svc._HTTP_TIMEOUT_SEC > float(
        __import__("os").environ.get("LUMOGIS_STACK_STATUS_DF_TIMEOUT_SEC", "30")
    )


def test_stack_status_stack_control_unreachable_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_get_stack_control_payload", lambda: (None, False, None))
    monkeypatch.setattr(svc, "_restart_secret", lambda: "secret")
    monkeypatch.setattr(
        svc,
        "_build_store_pings",
        lambda: {"postgres": AdminDiagnosticsStoreItem(name="postgres", status="ok", message=None)},
    )
    monkeypatch.setattr(svc, "_ollama_read_only_block", lambda: ([], []))

    resp = svc.build_stack_status_response()
    assert resp.meta.stack_control_reachable is False
    codes = {w.code for w in resp.warnings}
    assert "stack_control_unreachable" in codes


def test_ollama_block_empty_when_client_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import ollama_client

    monkeypatch.setattr(ollama_client, "list_local_models", lambda **_: [])
    models, warnings = svc._ollama_read_only_block()
    assert models == []
    assert any(w.code == "ollama_unreachable" for w in warnings)


def test_stack_status_single_flight_one_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"compose_ps": [], "system_df": None, "fetched_at": "2026-01-01T00:00:00Z"}
    barrier = {"n": 0}

    def slow_fetch() -> tuple[dict, bool]:
        barrier["n"] += 1
        time.sleep(0.05)
        return payload, True

    monkeypatch.setattr(svc, "_CACHE_TTL_SEC", 0.0)
    monkeypatch.setattr(svc, "_fetch_stack_control_status_unlocked", slow_fetch)
    monkeypatch.setattr(svc, "_restart_secret", lambda: "secret")
    monkeypatch.setattr(svc, "_build_store_pings", lambda: {})
    monkeypatch.setattr(svc, "_local_storage_rows", lambda _df: [])
    monkeypatch.setattr(svc, "_ollama_read_only_block", lambda: ([], []))

    import threading

    results: list = []

    def run() -> None:
        results.append(svc.build_stack_status_response())

    t1 = threading.Thread(target=run)
    t2 = threading.Thread(target=run)
    t1.start()
    time.sleep(0.01)
    t2.start()
    t1.join()
    t2.join()
    assert barrier["n"] >= 1
