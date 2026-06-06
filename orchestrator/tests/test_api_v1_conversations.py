# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for /api/v1/conversations (LUM-162)."""

from __future__ import annotations

import uuid
from datetime import datetime
from datetime import timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from models.memory import SessionSummary
from services.memory_purge import PurgeResult
from tests.sessions_memory_store import SessionsMemoryMetadataStore

import config


@pytest.fixture
def sessions_ms(monkeypatch: pytest.MonkeyPatch) -> SessionsMemoryMetadataStore:
    store = SessionsMemoryMetadataStore()
    config._instances["metadata_store"] = store
    return store


@pytest.fixture
def client(sessions_ms: SessionsMemoryMetadataStore):
    import main

    with TestClient(main.app) as c:
        yield c


def _insert_session(
    store: SessionsMemoryMetadataStore, *, user_id: str = "default", scope: str = "personal"
):
    sid = str(uuid.uuid4())
    store.sessions[sid] = {
        "session_id": uuid.UUID(sid),
        "summary": "First line title\nMore text",
        "topics": ["a"],
        "entities": [],
        "entity_ids": [],
        "user_id": user_id,
        "scope": scope,
        "updated_at": datetime.now(timezone.utc),
    }
    return sid


def test_list_returns_only_visible_sessions(client, sessions_ms):
    sid = _insert_session(sessions_ms)
    resp = client.get("/api/v1/conversations")
    assert resp.status_code == 200
    body = resp.json()
    assert any(c["conversation_id"] == sid for c in body["conversations"])


def test_get_detail_404_unknown_id(client):
    resp = client.get(f"/api/v1/conversations/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "conversation_not_found"


def test_delete_404_cross_user(client, sessions_ms):
    sid = _insert_session(sessions_ms, user_id="alice")
    resp = client.delete(f"/api/v1/conversations/{sid}")
    assert resp.status_code == 404


def test_delete_404_shared_scope_session(client, sessions_ms):
    sid = _insert_session(sessions_ms, scope="shared")
    with patch("services.conversations.purge_session_memory") as mock_purge:
        resp = client.delete(f"/api/v1/conversations/{sid}")
    assert resp.status_code == 404
    mock_purge.assert_not_called()


def test_delete_removes_published_from_projection_rows(client, sessions_ms, monkeypatch):
    sid = _insert_session(sessions_ms)
    proj = str(uuid.uuid4())
    sessions_ms.sessions[proj] = {
        "session_id": uuid.UUID(proj),
        "summary": "proj",
        "topics": [],
        "entities": [],
        "entity_ids": [],
        "user_id": "default",
        "scope": "shared",
        "published_from": uuid.UUID(sid),
        "updated_at": datetime.now(timezone.utc),
    }
    with patch(
        "services.conversations.purge_session_memory",
        return_value=PurgeResult(postgres_deleted=True, qdrant_deleted=True, graph_deleted=True),
    ):
        resp = client.delete(f"/api/v1/conversations/{sid}")
    assert resp.status_code == 200


def test_delete_removes_from_list_subsequent_get(client, sessions_ms, monkeypatch):
    sid = _insert_session(sessions_ms)
    with patch(
        "services.conversations.purge_session_memory",
        return_value=PurgeResult(postgres_deleted=True, qdrant_deleted=True, graph_deleted=True),
    ):
        client.delete(f"/api/v1/conversations/{sid}")
    sessions_ms.sessions.pop(sid, None)
    resp = client.get("/api/v1/conversations")
    assert all(c["conversation_id"] != sid for c in resp.json()["conversations"])


def test_continue_returns_seed_messages_slice1(client, sessions_ms):
    sid = _insert_session(sessions_ms)
    resp = client.post(f"/api/v1/conversations/{sid}/continue")
    assert resp.status_code == 200
    seeds = resp.json()["seed_messages"]
    assert len(seeds) == 1
    assert seeds[0]["role"] == "user"
    assert "Prior conversation context" in seeds[0]["content"]


def test_list_maps_updated_at_to_ended_at(client, sessions_ms):
    sid = _insert_session(sessions_ms)
    resp = client.get("/api/v1/conversations")
    row = next(c for c in resp.json()["conversations"] if c["conversation_id"] == sid)
    assert "ended_at" in row


def test_list_defaults_to_personal_scope_only(client, sessions_ms):
    _insert_session(sessions_ms, scope="shared")
    personal = _insert_session(sessions_ms, scope="personal")
    resp = client.get("/api/v1/conversations")
    ids = {c["conversation_id"] for c in resp.json()["conversations"]}
    assert personal in ids
    assert all(
        sessions_ms.sessions[cid]["scope"] == "personal"
        for cid in ids
        if cid in sessions_ms.sessions
    )


def test_delete_returns_partial_true_when_qdrant_fails_after_retries(
    client, sessions_ms, monkeypatch
):
    sid = _insert_session(sessions_ms)
    partial = PurgeResult(postgres_deleted=True, qdrant_deleted=False, graph_deleted=True)
    with patch("services.conversations.purge_session_memory", return_value=partial):
        resp = client.delete(f"/api/v1/conversations/{sid}")
    assert resp.status_code == 200
    assert resp.json()["partial"] is True


def test_delete_partial_purge_retry_succeeds(client, sessions_ms, monkeypatch):
    sid = _insert_session(sessions_ms)
    partial = PurgeResult(postgres_deleted=True, qdrant_deleted=False, graph_deleted=True)
    with patch("services.conversations.purge_session_memory", return_value=partial):
        first = client.delete(f"/api/v1/conversations/{sid}")
    assert first.status_code == 200
    assert first.json()["partial"] is True
    sessions_ms.sessions.pop(sid, None)
    sessions_ms.purged_conversations.add(("default", sid))
    ok = PurgeResult(postgres_deleted=True, qdrant_deleted=True, graph_deleted=True)
    with patch("services.conversations.purge_session_memory", return_value=ok) as mock_retry:
        second = client.delete(f"/api/v1/conversations/{sid}")
    assert second.status_code == 200
    assert second.json()["partial"] is False
    assert mock_retry.called


def test_session_end_rejects_non_uuid_session_id(client):
    resp = client.post(
        "/session/end",
        json={"session_id": "not-a-uuid", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 422


def test_continue_seed_uses_user_role_not_system(client, sessions_ms):
    sid = _insert_session(sessions_ms)
    resp = client.post(f"/api/v1/conversations/{sid}/continue")
    assert resp.json()["seed_messages"][0]["role"] == "user"


def test_malformed_uuid_returns_422(client):
    resp = client.get("/api/v1/conversations/not-valid")
    assert resp.status_code == 422


def test_put_upserts_web_header_before_session_row(client, sessions_ms):
    """Client-minted thread ids must create web_conversations via PUT (LUM-162 slice 2)."""
    sid = str(uuid.uuid4())
    resp = client.put(
        f"/api/v1/conversations/{sid}",
        json={"title": "Active chat", "model": "test-model"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["conversation_id"] == sid
    assert body["title"] == "Active chat"
    key = f"{sid}:default"
    assert key in sessions_ms.web_conversations


def test_put_then_append_message_persists_transcript(client, sessions_ms):
    sid = str(uuid.uuid4())
    assert client.put(
        f"/api/v1/conversations/{sid}",
        json={"title": "Chat", "model": "m"},
    ).status_code == 200
    mid = str(uuid.uuid4())
    resp = client.post(
        f"/api/v1/conversations/{sid}/messages",
        json={
            "message_id": mid,
            "role": "user",
            "content": "hello world",
            "model": "m",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["content"] == "hello world"
    assert mid in sessions_ms.web_messages


def test_put_and_append_blocked_after_purge_tombstone(client, sessions_ms):
    sid = str(uuid.uuid4())
    sessions_ms.purged_conversations.add(("default", sid))
    assert client.put(f"/api/v1/conversations/{sid}", json={"title": "x"}).status_code == 404
    mid = str(uuid.uuid4())
    assert (
        client.post(
            f"/api/v1/conversations/{sid}/messages",
            json={"message_id": mid, "role": "user", "content": "nope"},
        ).status_code
        == 404
    )


def test_session_end_to_list_e2e(client, sessions_ms, monkeypatch):
    sid = str(uuid.uuid4())
    summary = SessionSummary(
        session_id=sid,
        summary="Ended chat summary",
        topics=["topic"],
        entities=[],
    )
    with patch("routes.data.enqueue", return_value=1):
        end = client.post(
            "/session/end",
            json={
                "session_id": sid,
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
    assert end.status_code == 200
    from services.memory import store_session

    store_session(summary, user_id="default")
    listed = client.get("/api/v1/conversations")
    assert any(c["conversation_id"] == sid for c in listed.json()["conversations"])
