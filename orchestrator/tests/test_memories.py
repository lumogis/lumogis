# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for services/memories.py (LUM-291)."""

from datetime import datetime
from datetime import timezone
from unittest.mock import Mock

from services import memories


def test_store_memory_persists_and_embeds():
    ms = Mock()
    emb = Mock()
    emb.embed.return_value = [0.1] * 8
    vs = Mock()
    mid = memories.store_memory(
        user_id="u1",
        bank="coding",
        content="hello world",
        tags=["t"],
        metadata={"k": "v"},
        ms=ms,
        embedder=emb,
        vs=vs,
    )
    assert isinstance(mid, str) and mid
    sql, params = ms.execute.call_args.args
    assert "INSERT INTO memories" in sql
    assert "u1" in params and "coding" in params
    vs.upsert.assert_called_once()
    assert vs.upsert.call_args.kwargs["payload"] == {
        "memory_id": mid,
        "user_id": "u1",
        "bank": "coding",
    }


def test_store_memory_qdrant_failure_degrades():
    """Postgres is the SoR: a Qdrant failure must not lose the row or raise."""
    ms = Mock()
    emb = Mock()
    emb.embed.return_value = [0.1]
    vs = Mock()
    vs.upsert.side_effect = RuntimeError("qdrant down")
    mid = memories.store_memory(user_id="u", bank="b", content="x", ms=ms, embedder=emb, vs=vs)
    assert isinstance(mid, str)
    ms.execute.assert_called_once()


def test_get_memory_none_when_absent():
    ms = Mock()
    ms.fetch_one.return_value = None
    assert memories.get_memory("nope", user_id="u", ms=ms) is None


def test_get_memory_maps_row():
    now = datetime.now(timezone.utc)
    ms = Mock()
    ms.fetch_one.return_value = {
        "id": "m",
        "user_id": "u",
        "bank": "coding",
        "content": "c",
        "tags": ["a"],
        "metadata": {"x": 1},
        "valid_from": now,
        "valid_until": None,
        "created_at": now,
    }
    row = memories.get_memory("m", user_id="u", ms=ms)
    assert row is not None
    assert row.id == "m" and row.bank == "coding"
    assert row.metadata == {"x": 1} and row.valid_until is None


def test_get_memory_cross_user_isolation():
    """User B cannot read user A's memory — get_memory is user_id-scoped."""

    class _FakeStore:
        def fetch_one(self, sql, params=None):
            mid, uid = params  # query binds (memory_id, user_id)
            if uid == "alice":
                return {
                    "id": mid,
                    "user_id": "alice",
                    "bank": "coding",
                    "content": "secret",
                    "tags": [],
                    "metadata": {},
                    "valid_from": None,
                    "valid_until": None,
                    "created_at": None,
                }
            return None

    ms = _FakeStore()
    assert memories.get_memory("m1", user_id="alice", ms=ms) is not None
    assert memories.get_memory("m1", user_id="bob", ms=ms) is None
