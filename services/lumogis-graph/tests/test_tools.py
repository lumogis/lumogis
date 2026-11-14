# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for `routes/tools.py` (capability invoke contract v1 — LUM-41).

Contract under test:
  * POST /tools/query_graph requires the same bearer token as /webhook.
  * Request is the v1 envelope; the handler reads `arguments`.
  * Success returns 200 with `{"ok": true, "output": <handler return>}`.
  * Tool-level failures (timeout, budget exceeded, internal) return 200 with
    `{"ok": false, "error": {code, message, retryable}}` — so `retryable`
    survives to Core. Only transport-level auth failures stay non-200.
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


def _envelope(arguments: dict) -> dict:
    return {
        "contract_version": "1.0",
        "tool": "graph.query",
        "arguments": arguments,
        "meta": {"user": "default", "request_id": "test-req-1"},
    }


@pytest.fixture(autouse=True)
def _open_webhook_auth(monkeypatch):
    monkeypatch.delenv("GRAPH_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("KG_ALLOW_INSECURE_WEBHOOKS", "true")


def test_tools_query_graph_returns_200_with_output(monkeypatch):
    import graph.query as gq

    monkeypatch.setattr(
        gq, "query_graph_tool", lambda spec, *, user_id: {"nodes": [{"id": "x"}], "edges": []}
    )

    r = _client_with_tools().post(
        "/tools/query_graph",
        json=_envelope({"mode": "ego", "entity": "x", "user_id": "alice"}),
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "output": {"nodes": [{"id": "x"}], "edges": []}}


def test_tools_query_graph_reads_arguments_not_input(monkeypatch):
    """The handler must dispatch on the envelope's `arguments`, not a bare body."""
    seen: dict = {}

    import graph.query as gq

    def _capture(spec, *, user_id):
        seen.update(spec)
        seen["__user_id"] = user_id
        return "ok"

    monkeypatch.setattr(gq, "query_graph_tool", _capture)

    r = _client_with_tools().post(
        "/tools/query_graph",
        json=_envelope({"mode": "mentions", "entity": "Ada", "user_id": "alice"}),
    )
    assert r.status_code == 200
    # user_id is popped from the arguments and handed over as the keyword (LUM-884).
    assert seen == {"mode": "mentions", "entity": "Ada", "__user_id": "alice"}


def test_tools_query_graph_requires_bearer_when_secret_set(monkeypatch):
    monkeypatch.setenv("GRAPH_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("KG_ALLOW_INSECURE_WEBHOOKS", "false")

    import graph.query as gq

    monkeypatch.setattr(gq, "query_graph_tool", lambda spec, *, user_id: {"ok": True})

    # No bearer → 401 (transport-level auth stays non-200)
    r1 = _client_with_tools().post("/tools/query_graph", json=_envelope({"user_id": "alice"}))
    assert r1.status_code == 401

    # Correct bearer → 200 success envelope
    r2 = _client_with_tools().post(
        "/tools/query_graph",
        headers={"Authorization": "Bearer s3cret"},
        json=_envelope({"user_id": "alice"}),
    )
    assert r2.status_code == 200
    assert r2.json()["ok"] is True


def test_tools_query_graph_timeout_error_returns_200_ok_false_timeout(monkeypatch):
    """TimeoutError → 200 with a structured `timeout` error (retryable)."""
    import graph.query as gq

    def _raise_timeout(spec, *, user_id):
        raise TimeoutError("graph too slow")

    monkeypatch.setattr(gq, "query_graph_tool", _raise_timeout)

    r = _client_with_tools().post("/tools/query_graph", json=_envelope({"user_id": "alice"}))
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "timeout"
    assert body["error"]["retryable"] is True


def test_tools_query_graph_budget_exceeded_returns_200_ok_false_timeout(monkeypatch):
    """Handler returns normally but past the 2 s budget → 200 `timeout` error.

    We shrink the budget and use a tiny real sleep inside the stubbed handler
    rather than patching `time.monotonic` (which bleeds into asyncio).
    """
    import graph.query as gq
    from routes import tools as tools_mod

    monkeypatch.setattr(tools_mod, "_QUERY_GRAPH_BUDGET_S", 0.001)

    def _slow_query(spec, *, user_id):
        time.sleep(0.05)  # 50 ms — well over the 1 ms test budget
        return {"ok": True}

    monkeypatch.setattr(gq, "query_graph_tool", _slow_query)

    r = _client_with_tools().post("/tools/query_graph", json=_envelope({"user_id": "alice"}))
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "timeout"
    assert body["error"]["retryable"] is True


def test_tools_query_graph_unexpected_error_returns_200_ok_false_internal(monkeypatch):
    """Any non-TimeoutError exception → 200 with a structured `internal` error."""
    import graph.query as gq

    def _boom(spec, *, user_id):
        raise RuntimeError("unrelated failure")

    monkeypatch.setattr(gq, "query_graph_tool", _boom)

    r = _client_with_tools().post("/tools/query_graph", json=_envelope({"user_id": "alice"}))
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "internal"
    assert body["error"]["retryable"] is False


def test_tools_query_graph_passes_user_id_as_keyword(monkeypatch):
    """The envelope's arguments.user_id arrives as the keyword, not inside input_."""
    import graph.query as gq

    seen: dict = {}

    def _capture(spec, *, user_id):
        seen["spec"] = dict(spec)
        seen["user_id"] = user_id
        return "ok"

    monkeypatch.setattr(gq, "query_graph_tool", _capture)
    r = _client_with_tools().post(
        "/tools/query_graph",
        json=_envelope({"mode": "ego", "entity": "Ada", "user_id": "bob"}),
    )
    assert r.json() == {"ok": True, "output": "ok"}
    assert seen["user_id"] == "bob"
    assert "user_id" not in seen["spec"]


@pytest.mark.parametrize(
    "arguments",
    [{"mode": "ego", "entity": "Ada"}, {"user_id": ""}, {"user_id": 7}],
)
def test_tools_query_graph_rejects_missing_user_id(monkeypatch, arguments):
    """No usable user_id → invalid_request envelope; the handler is never called."""
    import graph.query as gq

    called: list = []
    monkeypatch.setattr(gq, "query_graph_tool", lambda *a, **k: called.append(1) or "x")
    r = _client_with_tools().post("/tools/query_graph", json=_envelope(arguments))
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "invalid_request"
    assert body["error"]["retryable"] is False
    assert called == []
