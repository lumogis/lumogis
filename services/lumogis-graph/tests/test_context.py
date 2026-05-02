# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for `routes/context.py`.

Contract under test:
  * Auth shares the webhook secret matrix (already covered in test_webhook.py).
    This file focuses on the route's own behaviour.
  * Returns 503 when FalkorDB is unavailable (graph store is None).
  * Returns 200 with `fragments` from `graph.query.on_context_building`.
  * `max_fragments` caps the response.
  * `on_context_building` raising must NOT crash the route — empty list is
    a valid result.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client_with_context() -> TestClient:
    from routes.context import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _open_webhook_auth(monkeypatch):
    """Default to insecure-webhooks=true so tests focus on the route, not auth."""
    monkeypatch.delenv("GRAPH_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("KG_ALLOW_INSECURE_WEBHOOKS", "true")


def _stub_on_context_building(monkeypatch, fragments_to_emit: list[str]):
    """Replace `graph.query.on_context_building` with a stub that appends fixed fragments."""
    import graph.query as gq

    def _stub(query: str, context_fragments: list[str], **_kw) -> None:
        context_fragments.extend(fragments_to_emit)

    monkeypatch.setattr(gq, "on_context_building", _stub)


def test_context_returns_fragments(monkeypatch):
    _stub_on_context_building(monkeypatch, ["[Graph] Ada Lovelace — mathematician"])

    r = _client_with_context().post(
        "/context",
        json={"query": "Who was Ada Lovelace?", "user_id": "default", "max_fragments": 3},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body == {"fragments": ["[Graph] Ada Lovelace — mathematician"]}


def test_context_returns_empty_for_unknown_query(monkeypatch):
    _stub_on_context_building(monkeypatch, [])

    r = _client_with_context().post(
        "/context",
        json={"query": "nonsense bzzzzt", "user_id": "default"},
    )
    assert r.status_code == 200
    assert r.json() == {"fragments": []}


def test_context_returns_503_when_graph_store_unavailable(monkeypatch):
    """Route must short-circuit with 503 if FalkorDB is not configured."""
    import config

    monkeypatch.setattr(config, "get_graph_store", lambda: None)

    r = _client_with_context().post(
        "/context",
        json={"query": "anything", "user_id": "default"},
    )
    assert r.status_code == 503
    assert "graph store unavailable" in r.json()["detail"]


def test_context_caps_at_max_fragments(monkeypatch):
    """max_fragments must truncate the response (the contract is a cap, not the count)."""
    _stub_on_context_building(monkeypatch, [f"[Graph] frag-{i}" for i in range(10)])

    r = _client_with_context().post(
        "/context",
        json={"query": "x", "user_id": "default", "max_fragments": 3},
    )
    assert r.status_code == 200
    assert r.json()["fragments"] == ["[Graph] frag-0", "[Graph] frag-1", "[Graph] frag-2"]


def test_context_swallows_handler_exception(monkeypatch):
    """If `on_context_building` raises, return 200 with empty fragments — never bubble."""
    import graph.query as gq

    def _boom(query: str, context_fragments: list[str], **_kw) -> None:
        raise RuntimeError("seeded explosion")

    monkeypatch.setattr(gq, "on_context_building", _boom)

    r = _client_with_context().post("/context", json={"query": "x", "user_id": "default"})
    assert r.status_code == 200
    assert r.json() == {"fragments": []}


def test_context_rejects_max_fragments_zero():
    """Pydantic must reject max_fragments=0 (ge=1 in ContextRequest)."""
    r = _client_with_context().post(
        "/context",
        json={"query": "x", "user_id": "default", "max_fragments": 0},
    )
    assert r.status_code == 422
