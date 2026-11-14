# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``POST /graph/backfill`` and its background runner (LUM-54, was BL-033).

Ported from Core's ``orchestrator/tests/premium/test_graph_reconcile.py::
TestBackfillEndpoint``. That class was skipped once Core stopped mounting
``plugins.graph.routes``, which left the route with no unit coverage. The route
now lives in ``graph/routes.py`` here, so the tests mount its router on a bare
FastAPI app. The sibling ``POST /graph/backfill/temporal`` is covered in
``test_temporal.py``.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from graph import routes as graph_routes

import config


@pytest.fixture(autouse=True)
def _clean_backfill_env(monkeypatch):
    monkeypatch.delenv("GRAPH_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    graph_routes._backfill_running.clear()
    yield
    graph_routes._backfill_running.clear()


@pytest.fixture
def runs(monkeypatch) -> list:
    """Replace the background runner; record the limit it was called with.

    The fake clears the running flag like the real runner, so a test sees the
    state the route leaves behind after its background task.
    """
    calls: list = []

    def fake_run(limit_per_type):
        calls.append(limit_per_type)
        graph_routes._backfill_running.clear()

    monkeypatch.setattr(graph_routes, "_run_backfill", fake_run)
    return calls


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(graph_routes.router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Route contract: 202 / 409 / 503 / 401 / 403
# ---------------------------------------------------------------------------


def test_backfill_returns_202_and_starts_the_runner(runs):
    r = _client().post("/graph/backfill")
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "backfill_started"
    assert body["limit_per_type"] is None
    assert runs == [None]


def test_backfill_passes_limit_per_type_to_the_runner(runs):
    r = _client().post("/graph/backfill?limit_per_type=50")
    assert r.status_code == 202
    assert r.json()["limit_per_type"] == 50
    assert runs == [50]


def test_backfill_rejects_non_integer_limit(runs):
    r = _client().post("/graph/backfill?limit_per_type=lots")
    assert r.status_code == 422
    assert runs == []


def test_backfill_409_while_running(runs):
    graph_routes._backfill_running.set()  # a run is in progress
    r = _client().post("/graph/backfill")
    assert r.status_code == 409
    assert runs == []
    # The rejected request must not clear the running run's flag.
    assert graph_routes._backfill_running.is_set()


def test_backfill_503_when_graph_disabled(runs):
    config._instances["graph_store:personal"] = None
    r = _client().post("/graph/backfill")
    assert r.status_code == 503
    assert runs == []
    assert not graph_routes._backfill_running.is_set()


def test_backfill_401_when_auth_enabled_and_unauthenticated(runs, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    r = _client().post("/graph/backfill")
    assert r.status_code == 401
    assert runs == []


@pytest.mark.parametrize("headers", [{}, {"X-Graph-Admin-Token": "wrongtoken"}])
def test_backfill_403_when_admin_token_missing_or_wrong(runs, monkeypatch, headers):
    monkeypatch.setenv("GRAPH_ADMIN_TOKEN", "supersecret")
    r = _client().post("/graph/backfill", headers=headers)
    assert r.status_code == 403
    assert runs == []
    assert not graph_routes._backfill_running.is_set()


def test_backfill_202_when_admin_token_correct(runs, monkeypatch):
    monkeypatch.setenv("GRAPH_ADMIN_TOKEN", "supersecret")
    r = _client().post("/graph/backfill", headers={"X-Graph-Admin-Token": "supersecret"})
    assert r.status_code == 202
    assert runs == [None]


# ---------------------------------------------------------------------------
# _run_backfill: the running flag is always released
# ---------------------------------------------------------------------------


def test_run_backfill_clears_flag_after_success(monkeypatch):
    import graph.reconcile as reconcile

    seen: list = []
    monkeypatch.setattr(
        reconcile,
        "run_reconciliation",
        lambda limit_per_type=None: seen.append(limit_per_type) or {"totals": {"scanned": 3}},
    )
    graph_routes._backfill_running.set()
    graph_routes._run_backfill(7)
    assert seen == [7]
    assert not graph_routes._backfill_running.is_set()


def test_run_backfill_clears_flag_and_swallows_error(monkeypatch):
    import graph.reconcile as reconcile

    def boom(limit_per_type=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(reconcile, "run_reconciliation", boom)
    graph_routes._backfill_running.set()
    graph_routes._run_backfill(None)  # must not raise into the background task
    assert not graph_routes._backfill_running.is_set()


def test_second_backfill_accepted_after_first_completes():
    """End to end through the real runner on the mock stores: the flag set by
    the first request is cleared by its background task, so a second request
    is accepted rather than stuck at 409."""
    client = _client()
    assert client.post("/graph/backfill").status_code == 202
    assert not graph_routes._backfill_running.is_set()
    assert client.post("/graph/backfill").status_code == 202
