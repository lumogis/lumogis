# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Subprocess stdio cursor integration slice (LUM-299)."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from datetime import datetime
from datetime import timezone
from pathlib import Path

import pytest

from tests._stdio_harness import ORCHESTRATOR
from tests._stdio_harness import REPO_ROOT
from tests._stdio_harness import StdioTokenStore
from tests._stdio_harness import free_port
from tests._stdio_harness import mcp_stdio_roundtrip
from tests._stdio_harness import start_uvicorn
from tests._stdio_harness import stop_uvicorn

if str(ORCHESTRATOR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR))

FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "coding_bank.json"
TEST_USER = "cursor-stdio-user"
_AS_OF = datetime(2026, 6, 25, tzinfo=timezone.utc)


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


class _StdioIntegrationStore:
    """RoutesFakeStore + fixture-backed reads — no orchestrator.tests import."""

    def __init__(self, fixture: dict, *, user_id: str) -> None:
        self._token_store = StdioTokenStore()
        self._lock = threading.Lock()
        self._user_id = user_id
        self._memories: dict[str, dict] = {}
        for bank_name, bank in fixture["banks"].items():
            for mem in bank["memories"]:
                self._memories[mem["memory_id"]] = {
                    "id": mem["memory_id"],
                    "user_id": user_id,
                    "bank": bank_name,
                    "content": mem["content"],
                    "valid_from": _AS_OF,
                    "valid_until": None,
                }

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def transaction(self):
        return self._token_store.transaction()

    def execute(self, query: str, params=None) -> None:
        return self._token_store.execute(query, params)

    def fetch_one(self, query, params=None):
        if "COUNT" in (query or "").upper():
            return {"n": 1}
        row = self._token_store.fetch_one(query, params)
        if row is not None:
            return row
        return None

    def fetch_all(self, query, params=None):
        q = (query or "").lower()
        p = params or ()
        if "content_tsv" in q:
            user_id, bank, as_of, query_text = p[0], p[1], p[2], p[3]
            terms = [t.lower() for t in re.findall(r"\w+", str(query_text)) if len(t) > 2]
            scored: list[tuple[int, str]] = []
            for mem in self._memories.values():
                if mem["user_id"] != user_id or mem["bank"] != bank:
                    continue
                if mem.get("valid_until") is not None and mem["valid_until"] < as_of:
                    continue
                score = sum(1 for t in terms if t in mem["content"].lower())
                if score > 0:
                    scored.append((score, mem["id"]))
            scored.sort(key=lambda x: (-x[0], x[1]))
            return [{"id": mid} for _, mid in scored[:20]]
        if "order by valid_from desc" in q:
            user_id, bank, as_of = p[0], p[1], p[2]
            hits = [
                {"id": m["id"]}
                for m in self._memories.values()
                if m["user_id"] == user_id
                and m["bank"] == bank
                and (m.get("valid_until") is None or m["valid_until"] >= as_of)
            ]
            return hits[:20]
        if "select id, content" in q and "valid_from, valid_until" in q and "id = any" in q:
            ids, user_id, as_of = p[0], p[1], p[2]
            id_set = {str(i) for i in ids}
            rows = []
            for mem in self._memories.values():
                if mem["id"] not in id_set or mem["user_id"] != user_id:
                    continue
                if mem.get("valid_until") is not None and mem["valid_until"] < as_of:
                    continue
                row = {
                    "id": mem["id"],
                    "content": mem["content"],
                    "valid_from": mem.get("valid_from"),
                    "valid_until": mem.get("valid_until"),
                }
                if "bank" in q:
                    row["bank"] = mem.get("bank", "coding")
                rows.append(row)
            return rows
        if "id, content, valid_from" in q:
            ids, user_id, as_of = p[0], p[1], p[2]
            id_set = {str(i) for i in ids}
            return [
                {
                    "id": m["id"],
                    "content": m["content"],
                    "valid_from": m["valid_from"],
                    "valid_until": m.get("valid_until"),
                }
                for m in self._memories.values()
                if m["id"] in id_set and m["user_id"] == user_id
            ]
        return []


class _StdioFakeVS:
    def __init__(self, fixture: dict, *, user_id: str) -> None:
        self._hits = []
        for bank_name, bank in fixture["banks"].items():
            for mem in bank["memories"]:
                self._hits.append(
                    {
                        "id": f"pt-{mem['memory_id']}",
                        "score": 0.9,
                        "payload": {
                            "memory_id": mem["memory_id"],
                            "user_id": user_id,
                            "bank": bank_name,
                        },
                    }
                )

    def ping(self) -> bool:
        return True

    def create_collection(self, name: str, vector_size: int) -> None:
        pass

    def ensure_payload_index(self, collection: str, field: str) -> None:
        pass

    def search(self, collection, vector, limit, threshold, filter=None, sparse_query=None):
        must = (filter or {}).get("must") or []
        filt_user = filt_bank = None
        for clause in must:
            key = clause.get("key")
            val = (clause.get("match") or {}).get("value")
            if key == "user_id":
                filt_user = val
            if key == "bank":
                filt_bank = val
        out = []
        for h in self._hits:
            payload = h.get("payload") or {}
            if filt_user and payload.get("user_id") != filt_user:
                continue
            if filt_bank and payload.get("bank") != filt_bank:
                continue
            out.append(h)
        return out[:limit]


class _StdioFakeEmbedder:
    @property
    def vector_size(self) -> int:
        return 3

    def ping(self) -> bool:
        return True

    def embed(self, text):
        return [0.1, 0.2, 0.3]


def _mint_token(store, *, scopes: list[str]) -> str:
    from services import mcp_tokens as _mcp_tokens

    _mcp_tokens._LAST_STAMP_CACHE.clear()
    _row, plaintext = _mcp_tokens.mint(TEST_USER, "stdio-integration", scopes=scopes)
    return plaintext


@pytest.fixture
def core_app(monkeypatch):
    fixture = _load_fixture()
    monkeypatch.chdir(ORCHESTRATOR)
    config_dir = REPO_ROOT / "config"
    monkeypatch.setenv("MODELS_CONFIG", str(config_dir / "models.yaml"))
    monkeypatch.setenv("OLLAMA_CATALOG_FALLBACK", str(config_dir / "ollama_catalog_fallback.json"))
    monkeypatch.setenv("_LUMOGIS_TEST_SKIP_AUTH_CONSISTENCY_DO_NOT_SET_IN_PRODUCTION", "true")
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("MCP_DEFAULT_USER_ID", TEST_USER)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("LUMOGIS_INBOX_MODE", "off")
    monkeypatch.setenv("INGEST_PATHS_WATCH_MODE", "off")
    monkeypatch.setenv("GRAPH_MODE", "disabled")
    monkeypatch.setenv("RERANKER_BACKEND", "none")
    monkeypatch.setenv("LUMOGIS_DEFER_LIBRARY_INDEX", "1")
    monkeypatch.setenv("CAPABILITY_SERVICE_URLS", "")

    import config
    import services.mcp_write as mw

    store = _StdioIntegrationStore(fixture, user_id=TEST_USER)
    config._instances["metadata_store"] = store
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)
    monkeypatch.setattr(config, "get_embedder", lambda: _StdioFakeEmbedder())
    monkeypatch.setattr(
        config, "get_vector_store", lambda: _StdioFakeVS(fixture, user_id=TEST_USER)
    )
    monkeypatch.setattr(config, "get_recall_reranker", lambda: None)
    monkeypatch.setattr(mw, "extract_entities", lambda *a, **k: [])
    monkeypatch.setattr(mw, "extract_relations", lambda *a, **k: [])
    monkeypatch.setattr(
        mw,
        "add_memory",
        lambda **k: {"memory_id": "stdio-int-mem", "entity_ids": [], "relation_ids": []},
    )

    token = _mint_token(store, scopes=["mcp:read", "mcp:write"])

    import main

    port = free_port()
    server = start_uvicorn(main.app, port)
    yield port, token, fixture
    stop_uvicorn(server)


def test_stdio_bridge_preserves_readOnlyHint(core_app):
    port, token, _fixture = core_app
    env = _bridge_env(port, token)
    list_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    responses = _roundtrip(env, list_req)
    tools = responses[-1]["result"]["tools"]
    read_tools = {
        "memory.search",
        "memory.get_recent",
        "entity.lookup",
        "entity.search",
        "context.build",
        "recall",
    }
    for tool in tools:
        if tool["name"] in read_tools:
            assert tool.get("annotations", {}).get("readOnlyHint") is True


def test_stdio_recall_returns_expected_memory_id(core_app):
    port, token, fixture = core_app
    env = _bridge_env(port, token)
    recall_req = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "recall",
            "arguments": {
                "query": "why FalkorDB",
                "bank": "coding",
                "retrieval_strategies": ["semantic", "bm25", "temporal"],
            },
        },
    }
    responses = _roundtrip(env, recall_req)
    text = json.dumps(responses[-1])
    expected = fixture["recall_mappings"][0]["memory_ids"][0]
    assert expected in text, f"expected {expected} in recall result"


def test_stdio_add_memory_roundtrip(core_app):
    port, token, _fixture = core_app
    env = _bridge_env(port, token)
    write_req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "add_memory",
            "arguments": {"content": "stdio integration write", "bank": "coding"},
        },
    }
    responses = _roundtrip(env, write_req)
    assert "stdio-int-mem" in json.dumps(responses[-1])


def test_stdio_bank_isolation_personal_misses_coding(core_app):
    port, token, _fixture = core_app
    env = _bridge_env(port, token)
    recall_req = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "recall",
            "arguments": {
                "query": "why FalkorDB",
                "bank": "personal",
                "retrieval_strategies": ["semantic", "bm25", "temporal"],
            },
        },
    }
    responses = _roundtrip(env, recall_req)
    assert "cd-001" not in json.dumps(responses[-1])


def _bridge_env(port: int, token: str) -> dict:
    env = os.environ.copy()
    env["LUMOGIS_MCP_URL"] = f"http://127.0.0.1:{port}/mcp/"
    env["LUMOGIS_MCP_TOKEN"] = token
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT / "clients" / "lumogis-mcp"), str(ORCHESTRATOR)]
    )
    return env


def _roundtrip(env: dict, *requests: dict) -> list[dict]:
    init_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "lum299-stdio", "version": "0.1"},
        },
    }
    initialized = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    return mcp_stdio_roundtrip(env, [init_req, initialized, *requests])
