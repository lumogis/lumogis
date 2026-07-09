# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for the partial-purge reconciliation sweeper (LUM-416)."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from services.memory_purge import _SWEEPER_MAX_ATTEMPTS
from services.memory_purge import purge_session_memory
from services.memory_purge import sweep_partial_purges
from tests.sessions_memory_store import SessionsMemoryMetadataStore

import config


@pytest.fixture
def sessions_ms(monkeypatch: pytest.MonkeyPatch) -> SessionsMemoryMetadataStore:
    store = SessionsMemoryMetadataStore()
    config._instances["metadata_store"] = store
    return store


# ---------------------------------------------------------------------------
# purge_session_memory: qdrant_entities_deleted N/A fix (LUM-416 prerequisite)
# ---------------------------------------------------------------------------


def test_purge_session_memory_not_partial_when_all_arms_succeed(
    sessions_ms, mock_vector_store, monkeypatch
):
    """qdrant_entities_deleted must be True by default so a clean purge is not partial."""
    monkeypatch.setitem(config._instances, config._graph_store_cache_key("personal"), None)
    sid = str(uuid.uuid4())
    sessions_ms.sessions[sid] = {
        "session_id": uuid.UUID(sid),
        "summary": "test",
        "topics": [],
        "entities": [],
        "entity_ids": [],
        "user_id": "alice",
        "scope": "personal",
        "updated_at": None,
    }
    with patch("services.batch_queue.cancel_pending_session_end_jobs", return_value=0):
        result = purge_session_memory(user_id="alice", session_id=sid)
    assert result.postgres_deleted is True
    assert result.qdrant_deleted is True
    assert result.graph_deleted is True
    assert result.qdrant_entities_deleted is True
    assert result.partial is False


def test_purge_tombstone_resolved_at_set_on_full_success(
    sessions_ms, mock_vector_store, monkeypatch
):
    """resolved_at is persisted when a clean purge completes."""
    monkeypatch.setitem(config._instances, config._graph_store_cache_key("personal"), None)
    sid = str(uuid.uuid4())
    sessions_ms.sessions[sid] = {
        "session_id": uuid.UUID(sid),
        "summary": "test",
        "topics": [],
        "entities": [],
        "entity_ids": [],
        "user_id": "alice",
        "scope": "personal",
        "updated_at": None,
    }
    with patch("services.batch_queue.cancel_pending_session_end_jobs", return_value=0):
        purge_session_memory(user_id="alice", session_id=sid)
    entry = sessions_ms.purge_tombstone_data.get(("alice", sid))
    assert entry is not None
    assert entry["resolved_at"] is not None


def test_purge_tombstone_unresolved_when_qdrant_fails(sessions_ms, mock_vector_store, monkeypatch):
    """resolved_at stays None when the Qdrant arm exhausts its retries."""
    monkeypatch.setitem(config._instances, config._graph_store_cache_key("personal"), None)
    sid = str(uuid.uuid4())
    sessions_ms.sessions[sid] = {
        "session_id": uuid.UUID(sid),
        "summary": "test",
        "topics": [],
        "entities": [],
        "entity_ids": [],
        "user_id": "alice",
        "scope": "personal",
        "updated_at": None,
    }
    monkeypatch.setattr(
        mock_vector_store,
        "delete",
        lambda **_kw: (_ for _ in ()).throw(RuntimeError("qdrant down")),
    )

    with patch("services.batch_queue.cancel_pending_session_end_jobs", return_value=0):
        result = purge_session_memory(user_id="alice", session_id=sid)

    assert result.partial is True
    entry = sessions_ms.purge_tombstone_data.get(("alice", sid))
    assert entry is not None
    assert entry["resolved_at"] is None
    assert entry["qdrant_deleted"] is False


# ---------------------------------------------------------------------------
# sweep_partial_purges
# ---------------------------------------------------------------------------


def test_sweep_resolves_partial_tombstone(sessions_ms, mock_vector_store, monkeypatch):
    """Sweeper retries a partial tombstone and marks it resolved when arms succeed."""
    monkeypatch.setitem(config._instances, config._graph_store_cache_key("personal"), None)
    uid = "alice"
    sid = str(uuid.uuid4())
    # Seed a partial tombstone (Postgres done, Qdrant failed).
    sessions_ms.purged_conversations.add((uid, sid))
    sessions_ms.purge_tombstone_data[(uid, sid)] = {
        "user_id": uid,
        "session_id": sid,
        "qdrant_deleted": False,
        "graph_deleted": True,
        "errors": [],
        "sweep_attempts": 0,
        "resolved_at": None,
    }

    resolved = sweep_partial_purges()

    assert resolved == 1
    entry = sessions_ms.purge_tombstone_data[(uid, sid)]
    assert entry["resolved_at"] is not None
    assert entry["qdrant_deleted"] is True
    assert entry["sweep_attempts"] == 1


def test_sweep_returns_zero_when_no_partials(sessions_ms, mock_vector_store, monkeypatch):
    monkeypatch.setitem(config._instances, config._graph_store_cache_key("personal"), None)
    resolved = sweep_partial_purges()
    assert resolved == 0


def test_sweep_increments_attempts_on_continued_failure(
    sessions_ms, mock_vector_store, monkeypatch
):
    """Sweeper increments sweep_attempts even when the arm still fails."""
    monkeypatch.setitem(config._instances, config._graph_store_cache_key("personal"), None)
    uid = "bob"
    sid = str(uuid.uuid4())
    sessions_ms.purged_conversations.add((uid, sid))
    sessions_ms.purge_tombstone_data[(uid, sid)] = {
        "user_id": uid,
        "session_id": sid,
        "qdrant_deleted": False,
        "graph_deleted": True,
        "errors": [],
        "sweep_attempts": 2,
        "resolved_at": None,
    }
    monkeypatch.setattr(
        mock_vector_store,
        "delete",
        lambda **_kw: (_ for _ in ()).throw(RuntimeError("still down")),
    )

    resolved = sweep_partial_purges()

    assert resolved == 0
    entry = sessions_ms.purge_tombstone_data[(uid, sid)]
    assert entry["sweep_attempts"] == 3
    assert entry["resolved_at"] is None


def test_sweep_skips_tombstones_at_max_attempts(sessions_ms, mock_vector_store, monkeypatch):
    """Tombstones that hit the cap are excluded from sweep candidates."""
    monkeypatch.setitem(config._instances, config._graph_store_cache_key("personal"), None)
    uid = "carol"
    sid = str(uuid.uuid4())
    sessions_ms.purged_conversations.add((uid, sid))
    sessions_ms.purge_tombstone_data[(uid, sid)] = {
        "user_id": uid,
        "session_id": sid,
        "qdrant_deleted": False,
        "graph_deleted": True,
        "errors": [],
        "sweep_attempts": _SWEEPER_MAX_ATTEMPTS,  # already at cap
        "resolved_at": None,
    }

    resolved = sweep_partial_purges()

    assert resolved == 0
    entry = sessions_ms.purge_tombstone_data[(uid, sid)]
    assert entry["sweep_attempts"] == _SWEEPER_MAX_ATTEMPTS  # unchanged


def test_sweep_skips_already_resolved_tombstones(sessions_ms, mock_vector_store, monkeypatch):
    """Tombstones with resolved_at set are not re-processed."""
    from datetime import datetime
    from datetime import timezone

    monkeypatch.setitem(config._instances, config._graph_store_cache_key("personal"), None)
    uid = "dave"
    sid = str(uuid.uuid4())
    sessions_ms.purged_conversations.add((uid, sid))
    sessions_ms.purge_tombstone_data[(uid, sid)] = {
        "user_id": uid,
        "session_id": sid,
        "qdrant_deleted": True,
        "graph_deleted": True,
        "errors": [],
        "sweep_attempts": 1,
        "resolved_at": datetime.now(timezone.utc),  # already resolved
    }

    resolved = sweep_partial_purges()
    assert resolved == 0


def test_sweep_handles_multiple_partial_tombstones(sessions_ms, mock_vector_store, monkeypatch):
    """Sweeper resolves all eligible partial tombstones in one pass."""
    monkeypatch.setitem(config._instances, config._graph_store_cache_key("personal"), None)
    users = [("user1", str(uuid.uuid4())), ("user2", str(uuid.uuid4()))]
    for uid, sid in users:
        sessions_ms.purged_conversations.add((uid, sid))
        sessions_ms.purge_tombstone_data[(uid, sid)] = {
            "user_id": uid,
            "session_id": sid,
            "qdrant_deleted": False,
            "graph_deleted": True,
            "errors": [],
            "sweep_attempts": 0,
            "resolved_at": None,
        }

    resolved = sweep_partial_purges()

    assert resolved == 2
    for uid, sid in users:
        assert sessions_ms.purge_tombstone_data[(uid, sid)]["resolved_at"] is not None


def test_sweep_graph_arm_deletes_session_projections(sessions_ms, mock_vector_store, monkeypatch):
    """Sweeper must mirror sync purge and remove published Session projections (LUM-419)."""
    mock_graph = object()
    monkeypatch.setitem(config._instances, config._graph_store_cache_key("personal"), mock_graph)
    uid = "alice"
    sid = str(uuid.uuid4())
    sessions_ms.purged_conversations.add((uid, sid))
    sessions_ms.purge_tombstone_data[(uid, sid)] = {
        "user_id": uid,
        "session_id": sid,
        "qdrant_deleted": True,
        "graph_deleted": False,
        "errors": [],
        "sweep_attempts": 0,
        "resolved_at": None,
    }

    with (
        patch("plugins.graph.writer.delete_session") as mock_delete_session,
        patch("plugins.graph.writer.delete_session_projections") as mock_delete_projections,
    ):
        resolved = sweep_partial_purges()

    assert resolved == 1
    mock_delete_session.assert_called_once_with(mock_graph, session_id=sid, user_id=uid)
    mock_delete_projections.assert_called_once_with(mock_graph, source_session_id=sid)
