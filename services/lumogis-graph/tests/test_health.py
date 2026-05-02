# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for `routes/health.py`.

Contract under test:
  - GET /health is ALWAYS 200 (liveness must not flap on backend hiccups).
  - The `falkordb` and `postgres` boolean fields reflect probe outcome.
  - Probes never raise — exceptions inside the probe collapse to `False`.
  - `pending_webhook_tasks` mirrors `webhook_queue.qsize()` exactly.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client_with_health() -> TestClient:
    """Bare app with only the health router — no lifespan, no other routes."""
    from routes.health import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_health_returns_ok_when_backends_up(monkeypatch):
    """Both backends respond → 200 with both flags True and queue depth = 0."""
    import webhook_queue

    monkeypatch.setattr(webhook_queue, "qsize", lambda: 0)
    client = _client_with_health()
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert isinstance(body["version"], str) and body["version"]
    assert body["falkordb"] is True
    assert body["postgres"] is True
    assert body["pending_webhook_tasks"] == 0


def test_health_returns_ok_when_backends_down(monkeypatch):
    """Probe raises → flag goes to `False`, but the route still returns 200."""
    import webhook_queue
    from routes import health as health_mod

    monkeypatch.setattr(health_mod, "_check_falkordb", lambda: False)
    monkeypatch.setattr(health_mod, "_check_postgres", lambda: False)
    monkeypatch.setattr(webhook_queue, "qsize", lambda: 7)

    client = _client_with_health()
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["falkordb"] is False
    assert body["postgres"] is False
    assert body["pending_webhook_tasks"] == 7


def test_health_probe_swallows_exceptions(monkeypatch):
    """If `config.get_graph_store()` raises, the FalkorDB probe must return False."""
    import config
    from routes.health import _check_falkordb

    def _boom():
        raise RuntimeError("explosion in test")

    monkeypatch.setattr(config, "get_graph_store", _boom)
    assert _check_falkordb() is False
