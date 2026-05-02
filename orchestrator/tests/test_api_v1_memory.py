# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``/api/v1/memory/{search,recent}`` — happy path + degradation contract."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        c.app.state.embedding_ready = True
        yield c


def test_search_rejects_blank_q(client):
    resp = client.get("/api/v1/memory/search", params={"q": ""})
    assert resp.status_code == 422


def test_search_returns_hits_from_semantic_search(client, monkeypatch):
    fake_hit = SimpleNamespace(
        file_path="/scope/file.md",
        score=0.91,
        chunk_text="hello world snippet",
        metadata={
            "title": "File",
            "source": "ingest",
            "created_at": "2026-04-01T10:00:00+00:00",
            "scope": "personal",
            "owner_user_id": "default",
        },
    )

    def _fake_search(q, limit, user_id):
        assert q == "hello"
        assert limit == 5
        return [fake_hit]

    import services.search as ss

    monkeypatch.setattr(ss, "semantic_search", _fake_search)

    resp = client.get("/api/v1/memory/search", params={"q": "hello", "limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded"] is False
    assert len(body["hits"]) == 1
    h = body["hits"][0]
    assert h["score"] == pytest.approx(0.91)
    assert h["snippet"] == "hello world snippet"
    assert h["title"] == "File"


def test_search_degrades_when_embedder_not_ready(client):
    client.app.state.embedding_ready = False
    resp = client.get("/api/v1/memory/search", params={"q": "x"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded"] is True
    assert body["reason"] == "embedder_not_ready"
    assert body["hits"] == []


def test_search_degrades_when_vector_store_raises(client, monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("qdrant down")

    import services.search as ss

    monkeypatch.setattr(ss, "semantic_search", _boom)

    resp = client.get("/api/v1/memory/search", params={"q": "x"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded"] is True
    assert body["reason"] == "vector_store_unavailable"
    assert resp.headers.get("warning", "").startswith("199")


def test_recent_returns_sessions(client, monkeypatch):
    sess = SimpleNamespace(
        session_id="11111111-1111-4111-9111-111111111111",
        summary="trip notes",
        updated_at=datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc),
    )

    def _fake_recent(*, limit, user_id):
        assert limit == 3
        return [sess]

    import services.memory as mem

    monkeypatch.setattr(mem, "recent_sessions", _fake_recent)

    resp = client.get("/api/v1/memory/recent", params={"limit": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["sessions"]) == 1
    assert body["sessions"][0]["summary"] == "trip notes"


def test_recent_skips_sessions_without_timestamp(client, monkeypatch):
    sess = SimpleNamespace(session_id="x", summary="no time")

    def _fake_recent(*, limit, user_id):
        return [sess]

    import services.memory as mem

    monkeypatch.setattr(mem, "recent_sessions", _fake_recent)

    resp = client.get("/api/v1/memory/recent")
    assert resp.status_code == 200
    assert resp.json()["sessions"] == []
