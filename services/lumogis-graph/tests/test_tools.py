# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for `routes/tools.py`.

Contract under test:
  * POST /tools/query_graph requires the same bearer token as /webhook.
  * Successful invocation returns 200 with `{"output": <handler return>}`.
  * `query_graph_tool` raising `TimeoutError` returns 504 with reason=timeout.
  * Returning normally but past the 2 s budget returns 504 with
    reason=budget_exceeded (the route's wall-clock check is the canonical
    enforcement; the handler itself is not required to enforce the timer).
"""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client_with_tools() -> TestClient:
    from routes.tools import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _open_webhook_auth(monkeypatch):
    monkeypatch.delenv("GRAPH_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("KG_ALLOW_INSECURE_WEBHOOKS", "true")


def test_tools_query_graph_returns_200_with_output(monkeypatch):
    import graph.query as gq

    monkeypatch.setattr(
        gq, "query_graph_tool", lambda spec: {"nodes": [{"id": "x"}], "edges": []}
    )

    r = _client_with_tools().post(
        "/tools/query_graph",
        json={"input": {"mode": "ego", "entity": "x"}},
    )
    assert r.status_code == 200
    assert r.json() == {"output": {"nodes": [{"id": "x"}], "edges": []}}


def test_tools_query_graph_requires_bearer_when_secret_set(monkeypatch):
    monkeypatch.setenv("GRAPH_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("KG_ALLOW_INSECURE_WEBHOOKS", "false")

    import graph.query as gq

    monkeypatch.setattr(gq, "query_graph_tool", lambda spec: {"ok": True})

    # No bearer → 401
    r1 = _client_with_tools().post("/tools/query_graph", json={"input": {}})
    assert r1.status_code == 401

    # Correct bearer → 200
    r2 = _client_with_tools().post(
        "/tools/query_graph",
        headers={"Authorization": "Bearer s3cret"},
        json={"input": {}},
    )
    assert r2.status_code == 200


def test_tools_query_graph_returns_504_on_timeout_error(monkeypatch):
    """If the handler raises TimeoutError, route returns 504 with reason=timeout."""
    import graph.query as gq

    def _raise_timeout(spec):
        raise TimeoutError("graph too slow")

    monkeypatch.setattr(gq, "query_graph_tool", _raise_timeout)

    r = _client_with_tools().post("/tools/query_graph", json={"input": {}})
    assert r.status_code == 504
    body = r.json()
    assert body["detail"] == "query_graph: graph service unavailable"
    assert body["reason"] == "timeout"
    assert "elapsed_ms" in body


def test_tools_query_graph_returns_504_when_budget_exceeded(monkeypatch):
    """Handler returns normally but past the 2 s budget → 504 reason=budget_exceeded.

    We do NOT patch `time.monotonic` here because the route's `time` is the
    global `time` module — patching it bleeds into asyncio's event loop,
    which calls `time.monotonic` many times per request and can hang or
    misbehave with a non-real clock. Instead we shrink the budget itself
    and use a tiny real sleep inside the stubbed handler. Test runtime ~10 ms.
    """
    import graph.query as gq
    from routes import tools as tools_mod

    monkeypatch.setattr(tools_mod, "_QUERY_GRAPH_BUDGET_S", 0.001)

    def _slow_query(spec):
        time.sleep(0.05)  # 50 ms — well over the 1 ms test budget
        return {"ok": True}

    monkeypatch.setattr(gq, "query_graph_tool", _slow_query)

    r = _client_with_tools().post("/tools/query_graph", json={"input": {}})
    assert r.status_code == 504
    body = r.json()
    assert body["reason"] == "budget_exceeded"
    assert body["elapsed_ms"] >= 1


def test_tools_query_graph_returns_500_on_unexpected_error(monkeypatch):
    """Any non-TimeoutError exception in the handler → 500, not leaked."""
    import graph.query as gq

    def _boom(spec):
        raise RuntimeError("unrelated failure")

    monkeypatch.setattr(gq, "query_graph_tool", _boom)

    r = _client_with_tools().post("/tools/query_graph", json={"input": {}})
    assert r.status_code == 500
    assert r.json()["detail"] == "query_graph: internal error"
