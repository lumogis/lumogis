# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for services/memory_purge.py (LUM-162)."""

from __future__ import annotations

import uuid

import pytest
from services.memory_purge import purge_session_memory
from services.point_ids import note_conversation_point_id
from services.point_ids import session_conversation_point_id
from services.projection import projection_point_id
from tests.sessions_memory_store import SessionsMemoryMetadataStore

import config


@pytest.fixture
def sessions_ms(monkeypatch: pytest.MonkeyPatch) -> SessionsMemoryMetadataStore:
    store = SessionsMemoryMetadataStore()
    config._instances["metadata_store"] = store
    return store


def test_purge_removes_postgres_session_row(
    sessions_ms, mock_vector_store, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    sid = str(uuid.uuid4())
    sessions_ms.sessions[sid] = {
        "session_id": uuid.UUID(sid),
        "summary": "hello",
        "topics": [],
        "entities": [],
        "entity_ids": [],
        "user_id": "alice",
        "scope": "personal",
        "updated_at": None,
    }
    result = purge_session_memory(user_id="alice", session_id=sid)
    assert result.postgres_deleted is True
    assert sid not in sessions_ms.sessions


def test_purge_deletes_qdrant_point_by_deterministic_id(sessions_ms, mock_vector_store):
    config._instances[config._graph_store_cache_key("personal")] = None
    sid = str(uuid.uuid4())
    uid = "alice"
    pid = session_conversation_point_id(uid, sid)
    mock_vector_store.upsert(
        collection="conversations",
        id=pid,
        vector=[0.0] * 768,
        payload={"session_id": sid, "user_id": uid, "scope": "personal"},
    )
    sessions_ms.sessions[sid] = {
        "session_id": uuid.UUID(sid),
        "summary": "x",
        "topics": [],
        "entities": [],
        "entity_ids": [],
        "user_id": uid,
        "scope": "personal",
        "updated_at": None,
    }
    result = purge_session_memory(user_id=uid, session_id=sid)
    assert result.qdrant_deleted is True
    assert mock_vector_store.count("conversations") == 0


def test_purge_noop_graph_when_disabled(sessions_ms, mock_vector_store, monkeypatch):
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    sid = str(uuid.uuid4())
    sessions_ms.sessions[sid] = {
        "session_id": uuid.UUID(sid),
        "summary": "x",
        "topics": [],
        "entities": [],
        "entity_ids": [],
        "user_id": "alice",
        "scope": "personal",
        "updated_at": None,
    }
    result = purge_session_memory(user_id="alice", session_id=sid)
    assert result.graph_deleted is True


def test_purge_does_not_delete_note_conversation_points(
    sessions_ms, mock_vector_store, monkeypatch
):
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    sid = str(uuid.uuid4())
    note_pid = note_conversation_point_id("alice", str(uuid.uuid4()))
    mock_vector_store.upsert(
        collection="conversations",
        id=note_pid,
        vector=[0.0] * 768,
        payload={"user_id": "alice"},
    )
    sessions_ms.sessions[sid] = {
        "session_id": uuid.UUID(sid),
        "summary": "x",
        "topics": [],
        "entities": [],
        "entity_ids": [],
        "user_id": "alice",
        "scope": "personal",
        "updated_at": None,
    }
    purge_session_memory(user_id="alice", session_id=sid)
    assert mock_vector_store.count("conversations") == 1
    assert mock_vector_store._collections["conversations"][0]["id"] == note_pid


def test_purge_wrong_user_no_delete(sessions_ms, mock_vector_store, monkeypatch):
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    sid = str(uuid.uuid4())
    sessions_ms.sessions[sid] = {
        "session_id": uuid.UUID(sid),
        "summary": "x",
        "topics": [],
        "entities": [],
        "entity_ids": [],
        "user_id": "alice",
        "scope": "personal",
        "updated_at": None,
    }
    result = purge_session_memory(user_id="bob", session_id=sid)
    assert result.postgres_deleted is False
    assert sid in sessions_ms.sessions


def test_purge_deletes_shared_and_system_projection_qdrant_points(
    sessions_ms, mock_vector_store, monkeypatch
):
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    sid = str(uuid.uuid4())
    uid = "alice"
    for scope in ("shared", "system"):
        pid = projection_point_id("conversations", sid, scope)
        mock_vector_store.upsert(
            collection="conversations",
            id=pid,
            vector=[0.0] * 768,
            payload={"scope": scope},
        )
    sessions_ms.sessions[sid] = {
        "session_id": uuid.UUID(sid),
        "summary": "x",
        "topics": [],
        "entities": [],
        "entity_ids": [],
        "user_id": uid,
        "scope": "personal",
        "updated_at": None,
    }
    result = purge_session_memory(user_id=uid, session_id=sid)
    assert result.qdrant_deleted is True
    assert mock_vector_store.count("conversations") == 0


def test_purge_postgres_transaction_rolls_back_on_failure(
    sessions_ms, mock_vector_store, monkeypatch
):
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    sid = str(uuid.uuid4())
    sessions_ms.sessions[sid] = {
        "session_id": uuid.UUID(sid),
        "summary": "x",
        "topics": [],
        "entities": [],
        "entity_ids": [],
        "user_id": "alice",
        "scope": "personal",
        "updated_at": None,
    }
    sessions_ms._fail_next_postgres = True
    result = purge_session_memory(user_id="alice", session_id=sid)
    assert result.postgres_deleted is False
    assert sid in sessions_ms.sessions


def test_purge_qdrant_retries_before_partial(sessions_ms, mock_vector_store, monkeypatch):
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), None
    )
    sid = str(uuid.uuid4())
    sessions_ms.sessions[sid] = {
        "session_id": uuid.UUID(sid),
        "summary": "x",
        "topics": [],
        "entities": [],
        "entity_ids": [],
        "user_id": "alice",
        "scope": "personal",
        "updated_at": None,
    }
    calls = {"n": 0}
    real_delete = mock_vector_store.delete

    def flaky_delete(collection, id):  # noqa: A002
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("qdrant flake")
        return real_delete(collection, id)

    monkeypatch.setattr(mock_vector_store, "delete", flaky_delete)
    result = purge_session_memory(user_id="alice", session_id=sid)
    assert result.postgres_deleted is True
    assert result.qdrant_deleted is True
