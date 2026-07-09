# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Cursor integration smoke test harness (LUM-299)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.cursor_integration.annotations import READ_TOOLS
from tests.cursor_integration.annotations import WRITE_TOOLS
from tests.cursor_integration.annotations import assert_annotation_matrix
from tests.cursor_integration.bank_isolation import random_isolation_queries
from tests.cursor_integration.fake_stores import build_fake_stores_from_fixture
from tests.cursor_integration.fixture_loader import load_coding_bank
from tests.cursor_integration.mcp_jsonrpc import call_tool
from tests.cursor_integration.mcp_jsonrpc import initialize_payload
from tests.cursor_integration.mcp_jsonrpc import list_tool_names
from tests.cursor_integration.mcp_jsonrpc import mcp_post
from tests.cursor_integration.p95 import measure_recall_p95_ms

REPO_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR = REPO_ROOT / "orchestrator"
FIXTURE = load_coding_bank()
TEST_USER = "cursor-integration-user"


@pytest.fixture
def harness(monkeypatch):
    """Bootstrap in-process MCP harness with fixture-backed fakes."""
    monkeypatch.chdir(ORCHESTRATOR)
    config_dir = REPO_ROOT / "config"
    monkeypatch.setenv("MODELS_CONFIG", str(config_dir / "models.yaml"))
    monkeypatch.setenv("OLLAMA_CATALOG_FALLBACK", str(config_dir / "ollama_catalog_fallback.json"))
    monkeypatch.setenv(
        "_LUMOGIS_TEST_SKIP_AUTH_CONSISTENCY_DO_NOT_SET_IN_PRODUCTION", "true"
    )
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("MCP_DEFAULT_USER_ID", TEST_USER)
    monkeypatch.setenv("AUTH_ENABLED", "false")

    import config
    from services import mcp_tokens as mcp_tokens_mod
    import services.mcp_write as mw

    store, vs, embedder = build_fake_stores_from_fixture(FIXTURE, user_id=TEST_USER)
    config._instances["metadata_store"] = store
    mcp_tokens_mod._LAST_STAMP_CACHE.clear()
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)
    monkeypatch.setattr(config, "get_vector_store", lambda: vs)
    monkeypatch.setattr(config, "get_embedder", lambda: embedder)
    monkeypatch.setattr(config, "get_recall_reranker", lambda: None)

    monkeypatch.setattr(mw, "extract_entities", lambda *a, **k: [])
    monkeypatch.setattr(mw, "extract_relations", lambda *a, **k: [])

    session = FIXTURE.session_summaries()[0]

    class _SessionHit:
        def __init__(self, data: dict) -> None:
            self.session_id = data["session_id"]
            self.summary = data["summary"]
            self.topics = data.get("topics") or []
            self.entities = data.get("entities") or []
            self.score = 0.95

    hit = _SessionHit(session)

    import services.memory as memory_svc
    import services.search as search_svc

    monkeypatch.setattr(
        memory_svc,
        "retrieve_context",
        lambda query, limit=3, user_id=TEST_USER, **kw: [hit],
    )
    monkeypatch.setattr(
        memory_svc,
        "recent_sessions",
        lambda limit=10, user_id=TEST_USER, **kw: [hit],
    )
    monkeypatch.setattr(search_svc, "semantic_search", lambda *a, **k: [])

    _row, plaintext = mcp_tokens_mod.mint(
        TEST_USER, "cursor-integration", scopes=["mcp:read", "mcp:write"]
    )
    headers = {"Authorization": f"Bearer {plaintext}"}

    import main

    with TestClient(main.app) as client:
        init = mcp_post(client, initialize_payload(), headers)
        assert init.status_code == 200, init.text
        yield client, headers, store, vs


def _result_text(resp: dict) -> str:
    return json.dumps(resp)


def _memory_ids_in_recall(resp: dict) -> set[str]:
    text = _result_text(resp)
    found: set[str] = set()
    for mid in FIXTURE.memory_ids("coding") + FIXTURE.memory_ids("personal"):
        if mid in text:
            found.add(mid)
    return found


# --- unit-level fixture / helper tests --------------------------------------


def test_fixture_loads_and_covers_entity_types():
    types = FIXTURE.coding_entity_types_present()
    assert types == {
        "CODING_DECISION", "CODING_CONVENTION", "COMPONENT", "FAILURE",
        "SESSION", "TASK", "LIBRARY",
    }
    assert len(FIXTURE.memory_ids("coding")) >= 45
    assert len(FIXTURE.memory_ids("personal")) >= 3


def test_invalid_fixture_version(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"version": 99, "banks": {"coding": {"memories": []}}}')
    with pytest.raises(ValueError, match="unsupported fixture version"):
        load_coding_bank(bad)


def test_bank_isolation_query_generator():
    pairs = random_isolation_queries(FIXTURE, n=10, seed=299)
    assert len(pairs) == 10
    for query, target, forbidden in pairs:
        assert target in ("coding", "personal")
        assert forbidden in ("coding", "personal")
        assert target != forbidden


# --- MCP integration breadth ------------------------------------------------


def test_list_tools_annotation_matrix(harness):
    client, headers, _store, _vs = harness
    resp = mcp_post(
        client,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        headers,
    )
    tools = resp.json()["result"]["tools"]
    assert_annotation_matrix(tools)
    names = list_tool_names(client, headers)
    assert names >= READ_TOOLS | WRITE_TOOLS


@pytest.mark.parametrize("tool_name", sorted(READ_TOOLS))
def test_read_tools_roundtrip(harness, tool_name):
    client, headers, _store, _vs = harness
    entities = FIXTURE.entities("coding")
    session = FIXTURE.session_summaries()[0]
    args_map = {
        "memory.search": {"query": session["topics"][0], "limit": 5},
        "memory.get_recent": {"limit": 5},
        "entity.lookup": {"name": entities[0]["name"]},
        "entity.search": {"query": entities[0]["name"][:4], "limit": 5},
        "context.build": {"query": "FalkorDB", "max_tokens": 500},
        "recall": {
            "query": "why FalkorDB",
            "bank": "coding",
            "retrieval_strategies": ["semantic", "bm25", "temporal"],
        },
    }
    resp = call_tool(client, headers, tool_name, args_map[tool_name])
    assert resp.get("result", {}).get("isError") is not True, resp
    text = _result_text(resp)
    if tool_name == "memory.search":
        assert session["session_id"] in text
    elif tool_name == "memory.get_recent":
        assert "sessions" in text
    elif tool_name == "entity.lookup":
        assert entities[0]["name"] in text
    elif tool_name == "entity.search":
        assert entities[0]["name"] in text
    elif tool_name == "context.build":
        assert "context" in text
    elif tool_name == "recall":
        assert "cd-001" in text


@pytest.mark.parametrize("tool_name", sorted(WRITE_TOOLS))
def test_write_tools_roundtrip(harness, tool_name):
    client, headers, store, _vs = harness
    edge = FIXTURE.edges("coding")[0]
    src_ent = next(e for e in FIXTURE.entities("coding") if e["entity_id"] == edge["src_entity_id"])
    dst_ent = next(e for e in FIXTURE.entities("coding") if e["entity_id"] == edge["dst_entity_id"])
    forget_id = "cd-003"
    update_id = "cd-004"

    args_map = {
        "add_memory": {"content": "Integration harness write roundtrip memory", "bank": "coding"},
        "add_entity": {"name": "HarnessComponent", "entity_type": "COMPONENT"},
        "add_relation": {
            "src": src_ent["name"],
            "dst": dst_ent["name"],
            "relation_type": edge["relation_type"],
            "bank": "coding",
        },
        "forget": {"memory_id": forget_id},
        "update_observation": {
            "memory_id": update_id,
            "content": "updated observation from integration harness",
        },
        "checkpoint": {"summary": "session end", "bank": "coding"},
    }
    resp = call_tool(client, headers, tool_name, args_map[tool_name])
    assert resp.get("result", {}).get("isError") is not True, resp
    text = _result_text(resp)
    if tool_name == "add_memory":
        assert "memory_id" in text
    elif tool_name == "add_entity":
        assert "entity_id" in text
    elif tool_name == "add_relation":
        assert "relation_id" in text
    elif tool_name == "forget":
        assert forget_id in text
    elif tool_name == "update_observation":
        assert "memory_id" in text
    elif tool_name == "checkpoint":
        assert "memory_id" in text


def test_forget_soft_archives_memory(harness):
    client, headers, _store, _vs = harness
    memory_id = "cd-005"
    before = call_tool(
        client,
        headers,
        "recall",
        {
            "query": "pytest in-process harness",
            "bank": "coding",
            "retrieval_strategies": ["semantic", "bm25", "temporal"],
        },
    )
    assert memory_id in _result_text(before)
    forget_resp = call_tool(client, headers, "forget", {"memory_id": memory_id})
    assert forget_resp.get("result", {}).get("isError") is not True
    after = call_tool(
        client,
        headers,
        "recall",
        {
            "query": "pytest in-process harness",
            "bank": "coding",
            "retrieval_strategies": ["semantic", "bm25", "temporal"],
        },
    )
    assert memory_id not in _memory_ids_in_recall(after)


def test_bank_isolation_ten_random_queries(harness):
    client, headers, _store, _vs = harness
    for query, target_bank, forbidden_bank in random_isolation_queries(FIXTURE, n=10, seed=299):
        target_resp = call_tool(
            client,
            headers,
            "recall",
            {
                "query": query,
                "bank": target_bank,
                "retrieval_strategies": ["semantic", "bm25", "temporal"],
            },
        )
        expected = FIXTURE.expected_recall_hits(query, target_bank)
        if expected:
            assert any(mid in _result_text(target_resp) for mid in expected)

        personal_resp = call_tool(
            client,
            headers,
            "recall",
            {
                "query": query,
                "bank": "personal",
                "retrieval_strategies": ["semantic", "bm25", "temporal"],
            },
        )
        for coding_mid in FIXTURE.expected_recall_hits(query, "coding"):
            assert coding_mid not in _memory_ids_in_recall(personal_resp)


def test_recall_personal_bank_misses_coding_memory(harness):
    client, headers, _store, _vs = harness
    resp = call_tool(
        client,
        headers,
        "recall",
        {
            "query": "why FalkorDB",
            "bank": "personal",
            "retrieval_strategies": ["semantic", "bm25", "temporal"],
        },
    )
    assert "cd-001" not in _memory_ids_in_recall(resp)


def test_recall_qdrant_filter_uses_must_shape(harness):
    client, headers, _store, vs = harness
    call_tool(
        client,
        headers,
        "recall",
        {
            "query": "FalkorDB",
            "bank": "coding",
            "retrieval_strategies": ["semantic", "bm25", "temporal"],
        },
    )
    assert vs.search_calls, "recall should invoke FakeVS.search"
    filt, _limit = vs.search_calls[0]
    keys = {c.get("key") for c in (filt or {}).get("must", [])}
    assert "user_id" in keys
    assert "bank" in keys


def test_recall_bm25_temporal_params_include_bank(harness):
    client, headers, store, _vs = harness
    call_tool(
        client,
        headers,
        "recall",
        {
            "query": "FalkorDB",
            "bank": "coding",
            "retrieval_strategies": ["semantic", "bm25", "temporal"],
        },
    )
    bm25_calls = [
        p for method, p in store.calls
        if method.startswith("fetch_all:") and p and "content_tsv" in method
    ]
    temporal_calls = [
        p for method, p in store.calls
        if method.startswith("fetch_all:") and p and "valid_from desc" in method
    ]
    assert bm25_calls, "expected bm25 fetch_all invocations"
    assert temporal_calls, "expected temporal fetch_all invocations"
    for params in bm25_calls + temporal_calls:
        assert params[0] == TEST_USER
        assert params[1] in ("coding", "personal")


@pytest.mark.skipif(
    not os.environ.get("LUMOGIS_CURSOR_INTEGRATION"),
    reason="p95 gate runs only under make test-cursor-integration",
)
def test_recall_p95_under_ceiling_default(harness):
    client, headers, _store, _vs = harness

    def _call():
        call_tool(
            client,
            headers,
            "recall",
            {
                "query": "FalkorDB",
                "bank": "coding",
                "retrieval_strategies": ["semantic", "bm25", "temporal"],
            },
        )

    p95 = measure_recall_p95_ms(_call, iterations=200, warmup=20)
    assert p95 < 500.0, f"p95 {p95:.1f}ms exceeds 500ms ceiling"


def test_ac_mapping_surface1_semantic(harness):
    client, headers, _store, _vs = harness
    session = FIXTURE.session_summaries()[0]
    search_resp = call_tool(
        client, headers, "memory.search", {"query": session["topics"][0], "limit": 5}
    )
    recent_resp = call_tool(client, headers, "memory.get_recent", {"limit": 5})
    recall_resp = call_tool(
        client,
        headers,
        "recall",
        {
            "query": "why FalkorDB",
            "bank": "coding",
            "retrieval_strategies": ["semantic", "bm25", "temporal"],
        },
    )
    assert "results" in _result_text(search_resp)
    assert "sessions" in _result_text(recent_resp)
    assert "cd-001" in _result_text(recall_resp)


def test_ac_mapping_surface2_semantic(harness):
    client, headers, _store, _vs = harness
    ent = FIXTURE.entities("coding")[0]
    lookup_resp = call_tool(client, headers, "entity.lookup", {"name": ent["name"]})
    search_resp = call_tool(
        client, headers, "entity.search", {"query": ent["name"][:4], "limit": 5}
    )
    assert ent["name"] in _result_text(lookup_resp)
    assert ent["name"] in _result_text(search_resp)


def test_read_scoped_token_denied_on_add_memory(monkeypatch):
    monkeypatch.chdir(ORCHESTRATOR)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("MCP_DEFAULT_USER_ID", TEST_USER)

    import config
    from services import mcp_tokens as mcp_tokens_mod
    import services.mcp_write as mw

    store, vs, embedder = build_fake_stores_from_fixture(FIXTURE, user_id=TEST_USER)
    config._instances["metadata_store"] = store
    mcp_tokens_mod._LAST_STAMP_CACHE.clear()
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)
    monkeypatch.setattr(config, "get_vector_store", lambda: vs)
    monkeypatch.setattr(config, "get_embedder", lambda: embedder)
    monkeypatch.setattr(config, "get_recall_reranker", lambda: None)

    called = {"n": 0}
    monkeypatch.setattr(mw, "add_memory", lambda **k: called.__setitem__("n", called["n"] + 1))

    _row, plaintext = mcp_tokens_mod.mint(TEST_USER, "read-only", scopes=["mcp:read"])
    headers = {"Authorization": f"Bearer {plaintext}"}

    import main

    with TestClient(main.app) as client:
        mcp_post(client, initialize_payload(), headers)
        resp = call_tool(
            client, headers, "add_memory", {"content": "denied", "bank": "coding"}
        )
    assert resp.get("result", {}).get("isError") is True
    assert called["n"] == 0
