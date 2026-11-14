# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route tests for POST /codegraph/reindex (LUM-301). Gating paths only —
no FalkorDB required (they return before ingest runs)."""

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client() -> TestClient:
    from routes.codegraph import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_reindex_requires_admin(monkeypatch):
    monkeypatch.setenv("GRAPH_ADMIN_TOKEN", "s3cret")
    resp = _client().post("/codegraph/reindex", json={})
    assert resp.status_code == 403


def test_reindex_rejects_wrong_and_non_ascii_admin_token(monkeypatch):
    monkeypatch.setenv("GRAPH_ADMIN_TOKEN", "s3cret")
    for token in (b"wrong", "\xe9t\xe9".encode("latin-1")):
        resp = _client().post("/codegraph/reindex", json={}, headers={"X-Graph-Admin-Token": token})
        assert resp.status_code == 403, token


def test_reindex_when_kg_disabled_returns_409(monkeypatch):
    monkeypatch.delenv("GRAPH_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("LUMOGIS_CODEGRAPH_ENABLED", "false")
    resp = _client().post("/codegraph/reindex", json={})
    assert resp.status_code == 409


def test_reindex_rejects_path_outside_roots(monkeypatch):
    monkeypatch.delenv("GRAPH_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("LUMOGIS_CODEGRAPH_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_CODEGRAPH_ROOTS", "/work/allowed")
    import config

    monkeypatch.setattr(config, "get_graph_store", lambda: object())
    resp = _client().post("/codegraph/reindex", json={"roots": ["/etc"]})
    assert resp.status_code == 400


def test_reindex_503_when_graph_store_absent(monkeypatch):
    monkeypatch.delenv("GRAPH_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("LUMOGIS_CODEGRAPH_ENABLED", "true")
    import config

    monkeypatch.setattr(config, "get_graph_store", lambda: None)
    resp = _client().post("/codegraph/reindex", json={})
    assert resp.status_code == 503
