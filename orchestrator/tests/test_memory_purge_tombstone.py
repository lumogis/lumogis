# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Regression tests for purge tombstone vs session_end race (LUM-162)."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from models.memory import SessionSummary
from models.sessions import SessionEndPayload
from services.batch_handlers.session_end import handle as session_end_handle
from services.memory import store_session
from services.memory_purge import conversation_purge_target_exists
from services.memory_purge import is_conversation_purged
from services.memory_purge import purge_session_memory
from services.point_ids import session_conversation_point_id
from tests.sessions_memory_store import SessionsMemoryMetadataStore

import config


@pytest.fixture
def sessions_ms(monkeypatch: pytest.MonkeyPatch) -> SessionsMemoryMetadataStore:
    store = SessionsMemoryMetadataStore()
    config._instances["metadata_store"] = store
    return store


def test_purge_inserts_tombstone(sessions_ms, mock_vector_store, monkeypatch):
    monkeypatch.setitem(config._instances, config._graph_store_cache_key("personal"), None)
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
    with patch("services.batch_queue.cancel_pending_session_end_jobs", return_value=0):
        purge_session_memory(user_id="alice", session_id=sid)
    assert is_conversation_purged(user_id="alice", session_id=sid)


def test_store_session_skipped_after_purge(sessions_ms, mock_vector_store, monkeypatch):
    monkeypatch.setitem(config._instances, config._graph_store_cache_key("personal"), None)
    sid = str(uuid.uuid4())
    sessions_ms.purged_conversations.add(("alice", sid))
    summary = SessionSummary(session_id=sid, summary="ghost", topics=[], entities=[])
    store_session(summary, user_id="alice")
    assert sid not in sessions_ms.sessions
    assert mock_vector_store.count("conversations") == 0


def test_session_end_handler_skips_purged_conversation(sessions_ms, monkeypatch):
    sid = str(uuid.uuid4())
    sessions_ms.purged_conversations.add(("alice", sid))
    payload = SessionEndPayload(
        session_id=sid,
        messages=[{"role": "user", "content": "hello"}],
    )
    with patch("services.batch_handlers.session_end.summarize_session") as mock_sum:
        session_end_handle(user_id="alice", job_id=1, payload=payload)
    mock_sum.assert_not_called()


def test_purge_after_delete_allows_qdrant_retry(sessions_ms, mock_vector_store, monkeypatch):
    monkeypatch.setitem(config._instances, config._graph_store_cache_key("personal"), None)
    sid = str(uuid.uuid4())
    uid = "alice"
    pid = session_conversation_point_id(uid, sid)
    mock_vector_store.upsert(
        collection="conversations",
        id=pid,
        vector=[0.0] * 768,
        payload={"session_id": sid, "user_id": uid, "scope": "personal"},
    )
    sessions_ms.purged_conversations.add((uid, sid))

    assert conversation_purge_target_exists(user_id=uid, session_id=sid)

    calls = {"n": 0}
    real_delete = mock_vector_store.delete

    def flaky_delete(collection, id):  # noqa: A002
        calls["n"] += 1
        if calls["n"] <= 3:
            raise RuntimeError("qdrant flake")
        return real_delete(collection, id)

    monkeypatch.setattr(mock_vector_store, "delete", flaky_delete)
    with patch("services.batch_queue.cancel_pending_session_end_jobs", return_value=0):
        first = purge_session_memory(user_id=uid, session_id=sid)
    assert first.postgres_deleted is True
    assert first.qdrant_deleted is False
    assert mock_vector_store.count("conversations") == 1

    with patch("services.batch_queue.cancel_pending_session_end_jobs", return_value=0):
        second = purge_session_memory(user_id=uid, session_id=sid)
    assert second.qdrant_deleted is True
    assert mock_vector_store.count("conversations") == 0


def test_purge_tombstone_retry_cleans_orphaned_web_rows(
    sessions_ms, mock_vector_store, monkeypatch
):
    """Retry after partial purge must delete web_* rows even without a sessions row."""
    monkeypatch.setitem(config._instances, config._graph_store_cache_key("personal"), None)
    sid = str(uuid.uuid4())
    uid = "alice"
    sessions_ms.purged_conversations.add((uid, sid))
    sessions_ms.web_conversations[f"{sid}:{uid}"] = {
        "conversation_id": uuid.UUID(sid),
        "user_id": uid,
        "title": "orphan",
        "model": "m",
        "message_count": 0,
        "updated_at": None,
    }
    with patch("services.batch_queue.cancel_pending_session_end_jobs", return_value=0):
        result = purge_session_memory(user_id=uid, session_id=sid)
    assert result.postgres_deleted is True
    assert f"{sid}:{uid}" not in sessions_ms.web_conversations


def test_purge_resurrection_race_blocked(sessions_ms, mock_vector_store, monkeypatch):
    """Simulate delete then in-flight session_end: tombstone blocks store_session."""
    monkeypatch.setitem(config._instances, config._graph_store_cache_key("personal"), None)
    sid = str(uuid.uuid4())
    uid = "alice"
    sessions_ms.sessions[sid] = {
        "session_id": uuid.UUID(sid),
        "summary": "before delete",
        "topics": [],
        "entities": [],
        "entity_ids": [],
        "user_id": uid,
        "scope": "personal",
        "updated_at": None,
    }
    with patch("services.batch_queue.cancel_pending_session_end_jobs", return_value=0):
        purge_session_memory(user_id=uid, session_id=sid)

    summary = SessionSummary(session_id=sid, summary="resurrect", topics=["x"], entities=[])
    store_session(summary, user_id=uid)
    assert sid not in sessions_ms.sessions
    assert mock_vector_store.count("conversations") == 0
