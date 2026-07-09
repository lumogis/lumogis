# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for the MCP supersede/archive tools (LUM-526).

The conftest `MockMetadataStore.execute` is a no-op and `fetch_one` returns
None, so these use injected Mock/fake stores (via `ms=`) or monkeypatch the
service collaborators and assert on call args / order — not DB round-trips.
"""

from datetime import datetime
from datetime import timezone
from unittest.mock import Mock

import pytest
from models.mcp_write import CheckpointInput
from models.mcp_write import ForgetInput
from models.mcp_write import UpdateObservationInput
from models.mcp_write import MemoryRow
from services import entity_edges
from services import mcp_write
from services import memories


def _memrow(mid="m1", user_id="u", bank="coding", valid_until=None) -> MemoryRow:
    now = datetime.now(timezone.utc)
    return MemoryRow(
        id=mid, user_id=user_id, bank=bank, content="c", tags=[], metadata={},
        valid_from=now, valid_until=valid_until, created_at=now,
    )


# --- archive helpers ---------------------------------------------------------

def test_archive_memory_sets_valid_until():
    ms = Mock()
    ms.fetch_one.return_value = {"valid_until": None}  # exists, active
    out = memories.archive_memory("m1", user_id="u", ms=ms)
    assert out is True
    sql, params = ms.execute.call_args.args
    assert "UPDATE memories SET valid_until = now()" in sql
    assert "user_id = %s" in sql and "valid_until IS NULL" in sql  # user-scoped + guarded
    assert params == ("m1", "u")


def test_archive_memory_idempotent_on_already_archived():
    ms = Mock()
    ms.fetch_one.return_value = {"valid_until": datetime.now(timezone.utc)}  # already archived
    out = memories.archive_memory("m1", user_id="u", ms=ms)
    assert out is False
    ms.execute.assert_not_called()  # no-op


def test_archive_memory_not_found_returns_false():
    ms = Mock()
    ms.fetch_one.return_value = None
    assert memories.archive_memory("nope", user_id="u", ms=ms) is False


def test_archive_edges_for_memory_by_evidence_id():
    ms = Mock()
    entity_edges.archive_edges_for_memory("m1", user_id="u", ms=ms)
    sql, params = ms.execute.call_args.args
    assert "UPDATE entity_edges SET valid_until = now()" in sql
    assert "evidence_id = %s" in sql
    assert params == ("m1", "u")


# --- forget ------------------------------------------------------------------

def test_forget_archives_memory_and_edges(monkeypatch):
    monkeypatch.setattr(mcp_write.memories, "get_memory", lambda *a, **k: _memrow())
    calls = []
    monkeypatch.setattr(
        mcp_write.entity_edges,
        "fetch_active_edges_for_memory",
        lambda mid, **k: calls.append(("fetch", mid)) or [],
    )
    monkeypatch.setattr(
        mcp_write.memories,
        "archive_memory",
        lambda mid, **k: calls.append(("mem", mid)) or True,
    )
    monkeypatch.setattr(
        mcp_write.entity_edges,
        "archive_edges_for_memory",
        lambda mid, **k: calls.append(("edges", mid)),
    )
    monkeypatch.setattr(
        mcp_write.entity_edges,
        "purge_graph_projections_for_edges",
        lambda edges, **k: calls.append(("purge", edges)),
    )
    out = mcp_write.forget(user_id="u", memory_id="m1")
    assert out == {"memory_id": "m1", "archived": True}
    assert calls == [("fetch", "m1"), ("mem", "m1"), ("edges", "m1"), ("purge", [])]


def test_forget_purges_falkordb_on_correct_bank(monkeypatch):
    monkeypatch.setattr(mcp_write.memories, "get_memory", lambda *a, **k: _memrow())
    monkeypatch.setattr(mcp_write.memories, "archive_memory", lambda *a, **k: True)
    monkeypatch.setattr(mcp_write.entity_edges, "archive_edges_for_memory", lambda *a, **k: None)
    edges = [
        {
            "bank": "coding",
            "src_entity_id": "s1",
            "dst_entity_id": "d1",
            "relation_type": "RELATES_TO",
        }
    ]
    monkeypatch.setattr(
        mcp_write.entity_edges,
        "fetch_active_edges_for_memory",
        lambda *a, **k: list(edges),
    )
    purged = []
    monkeypatch.setattr(
        mcp_write.entity_edges,
        "purge_graph_projections_for_edges",
        lambda active, **k: purged.append(active),
    )
    mcp_write.forget(user_id="u", memory_id="m1")
    assert purged == [edges]


def test_forget_other_user_memory_denied(monkeypatch):
    # get_memory is user-scoped → returns None for a non-owner → not found.
    monkeypatch.setattr(mcp_write.memories, "get_memory", lambda *a, **k: None)
    archived = []
    monkeypatch.setattr(mcp_write.memories, "archive_memory",
                        lambda *a, **k: archived.append(1))
    with pytest.raises(ValueError):
        mcp_write.forget(user_id="bob", memory_id="alices-mem")
    assert archived == []  # nothing archived


def test_forget_idempotent_on_already_archived(monkeypatch):
    monkeypatch.setattr(mcp_write.memories, "get_memory",
                        lambda *a, **k: _memrow(valid_until=datetime.now(timezone.utc)))
    monkeypatch.setattr(mcp_write.memories, "archive_memory", lambda *a, **k: False)
    monkeypatch.setattr(mcp_write.entity_edges, "fetch_active_edges_for_memory", lambda *a, **k: [])
    monkeypatch.setattr(mcp_write.entity_edges, "archive_edges_for_memory", lambda *a, **k: None)
    monkeypatch.setattr(mcp_write.entity_edges, "purge_graph_projections_for_edges", lambda *a, **k: None)
    out = mcp_write.forget(user_id="u", memory_id="m1")
    assert out == {"memory_id": "m1", "archived": True}


# --- update_observation ------------------------------------------------------

def test_update_observation_adds_before_archiving(monkeypatch):
    order = []
    monkeypatch.setattr(mcp_write.memories, "get_memory", lambda *a, **k: _memrow())

    def fake_add(**kw):
        order.append("add")
        # the supersedes pointer must be threaded onto the new memory
        assert kw["metadata"]["supersedes"] == "m1"
        assert kw["bank"] == "coding"
        return {"memory_id": "new1", "entity_ids": ["e"], "relation_ids": ["r"]}

    monkeypatch.setattr(mcp_write, "add_memory", fake_add)
    monkeypatch.setattr(
        mcp_write.entity_edges,
        "fetch_active_edges_for_memory",
        lambda *a, **k: order.append("fetch_edges") or [],
    )
    monkeypatch.setattr(mcp_write.memories, "archive_memory",
                        lambda *a, **k: order.append("archive_mem"))
    monkeypatch.setattr(mcp_write.entity_edges, "archive_edges_for_memory",
                        lambda *a, **k: order.append("archive_edges"))
    monkeypatch.setattr(
        mcp_write.entity_edges,
        "purge_graph_projections_for_edges",
        lambda *a, **k: order.append("purge_graph"),
    )

    out = mcp_write.update_observation(user_id="u", memory_id="m1", content="new")
    assert out == {"old_memory_id": "m1", "new_memory_id": "new1",
                   "entity_ids": ["e"], "relation_ids": ["r"]}
    assert order[0] == "add"  # add BEFORE any archive
    assert order.index("fetch_edges") < order.index("archive_mem")
    assert "archive_mem" in order and order.index("add") < order.index("archive_mem")
    assert "archive_edges" in order and order.index("add") < order.index("archive_edges")
    assert order.index("archive_edges") < order.index("purge_graph")


def test_update_observation_refuses_already_archived(monkeypatch):
    monkeypatch.setattr(mcp_write.memories, "get_memory",
                        lambda *a, **k: _memrow(valid_until=datetime.now(timezone.utc)))
    added = []
    monkeypatch.setattr(mcp_write, "add_memory", lambda **k: added.append(1))
    with pytest.raises(ValueError):
        mcp_write.update_observation(user_id="u", memory_id="m1", content="new")
    assert added == []  # no new memory written


def test_update_observation_missing_memory_errors(monkeypatch):
    monkeypatch.setattr(mcp_write.memories, "get_memory", lambda *a, **k: None)
    with pytest.raises(ValueError):
        mcp_write.update_observation(user_id="u", memory_id="ghost", content="x")


def test_update_observation_other_user_memory_denied(monkeypatch):
    # get_memory is user-scoped → returns None for a non-owner → not found,
    # and no new memory is written (cross-user supersede is blocked).
    monkeypatch.setattr(mcp_write.memories, "get_memory", lambda *a, **k: None)
    added = []
    monkeypatch.setattr(mcp_write, "add_memory", lambda **k: added.append(1))
    with pytest.raises(ValueError):
        mcp_write.update_observation(user_id="bob", memory_id="alices-mem", content="new")
    assert added == []  # nothing written


def test_update_observation_metadata_overflow_after_supersedes_rejected(monkeypatch):
    # Caller metadata that passes the input bound can still overflow once the
    # supersedes pointer is injected; the service must re-check and refuse
    # rather than silently storing an over-cap jsonb payload.
    monkeypatch.setattr(mcp_write.memories, "get_memory", lambda *a, **k: _memrow())
    added = []
    monkeypatch.setattr(mcp_write, "add_memory", lambda **k: added.append(1))
    big = {"note": "x" * 4080}  # under 4096 alone, over once supersedes is added
    with pytest.raises(ValueError):
        mcp_write.update_observation(user_id="u", memory_id="m1", content="new", metadata=big)
    assert added == []  # refused before the write


# --- checkpoint --------------------------------------------------------------

def test_checkpoint_writes_kind_marker(monkeypatch):
    captured = {}
    monkeypatch.setattr(mcp_write.memories, "store_memory",
                        lambda **k: captured.update(k) or "cp1")
    # checkpoint must NOT go through the extraction path (add_memory) — assert on
    # the real collaborator it would call, not extract_entities (which is only
    # reachable via add_memory and so unreachable from checkpoint anyway).
    add_called = []
    monkeypatch.setattr(mcp_write, "add_memory", lambda **k: add_called.append(1))
    # VERIFY-PLAN (LUM-294): checkpoint now also creates a SESSION entity — mock
    # store_entities so the test doesn't reach the embedder, and assert the new
    # return shape carries entity_id.
    sess = {}
    monkeypatch.setattr(mcp_write, "store_entities",
                        lambda entities, **k: sess.update(entity=entities[0], kw=k) or ["sess1"])
    out = mcp_write.checkpoint(user_id="u", bank="coding", summary="end of session")
    assert out["memory_id"] == "cp1" and out["entity_id"] == "sess1"
    assert captured["metadata"] == {"kind": "checkpoint", "source": "mcp"}
    assert captured["bank"] == "coding" and captured["user_id"] == "u"
    assert sess["entity"].entity_type == "SESSION" and sess["kw"]["evidence_id"] == "cp1"
    assert add_called == []  # no extract_entities/add_memory path taken


# --- scope enforcement (tool handlers) ---------------------------------------

def test_supersede_tools_denied_without_write_scope(monkeypatch):
    """Read-scoped tokens are denied AND the underlying writers never run."""
    import mcp_server

    called = {"n": 0}
    monkeypatch.setattr(mcp_write, "forget", lambda **k: called.__setitem__("n", called["n"] + 1))
    monkeypatch.setattr(mcp_write, "update_observation",
                        lambda **k: called.__setitem__("n", called["n"] + 1))
    monkeypatch.setattr(mcp_write, "checkpoint", lambda **k: called.__setitem__("n", called["n"] + 1))

    tok = mcp_server._set_current_mcp_scopes(["mcp:read"])
    try:
        with pytest.raises(mcp_server.McpScopeError):
            mcp_server.forget_tool(memory_id="m1")
        with pytest.raises(mcp_server.McpScopeError):
            mcp_server.update_observation_tool(memory_id="m1", content="x")
        with pytest.raises(mcp_server.McpScopeError):
            mcp_server.checkpoint_tool(summary="x")
    finally:
        mcp_server._reset_current_mcp_scopes(tok)
    assert called["n"] == 0  # no writer invoked past the scope gate


# --- input model bounds ------------------------------------------------------

def test_input_models_bounds():
    assert ForgetInput(memory_id="m1").memory_id == "m1"
    with pytest.raises(Exception):
        UpdateObservationInput(memory_id="m1", content="x" * 8001)
    with pytest.raises(Exception):
        CheckpointInput(summary="ok", bank="Bad Bank!")
