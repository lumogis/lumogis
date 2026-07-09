# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Transport smoke: subprocess stdio bridge against live Core ``/mcp/`` (TestClient ASGI)."""

from __future__ import annotations

import json
import os
import sys
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
from tests._stdio_harness import stub_orchestrator_lifespan_for_stdio

if str(ORCHESTRATOR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR))


class FakeMS:
    def __init__(self, results):
        self._results = list(results)

    def ping(self) -> bool:
        return True

    def fetch_one(self, query, params=None):
        if "COUNT" in query.upper():
            return {"n": 1}
        return None

    def fetch_all(self, query, params=None):
        q = query.lower()
        if self._results and any(
            token in q for token in ("memories", "entity_edges", "entities", "ts_rank")
        ):
            return self._results.pop(0)
        return []


class FakeEmbedder:
    @property
    def vector_size(self) -> int:
        return 3

    def ping(self) -> bool:
        return True

    def embed(self, text):
        return [0.1, 0.2, 0.3]


class FakeVS:
    def __init__(self, hits):
        self._hits = hits

    def ping(self) -> bool:
        return True

    def create_collection(self, name: str, vector_size: int) -> None:
        pass

    def ensure_payload_index(self, collection: str, field: str) -> None:
        pass

    def ensure_tenant_payload_index(self, collection: str, field: str) -> None:
        pass

    def search(self, collection, vector, limit, threshold, filter=None, sparse_query=None):
        return self._hits


def _install_token_store(monkeypatch, *, scopes):
    import config as _config
    from services import mcp_tokens as _mcp_tokens

    store = StdioTokenStore()
    _config._instances["metadata_store"] = store
    _mcp_tokens._LAST_STAMP_CACHE.clear()
    monkeypatch.setattr(
        _config, "get_metadata_store", lambda: _config._instances["metadata_store"]
    )
    _row, plaintext = _mcp_tokens.mint("stdio-smoke-user", "smoke", scopes=scopes)
    return plaintext


@pytest.fixture
def core_app(monkeypatch):
    """Core app with recall fakes and scoped MCP token store."""
    monkeypatch.chdir(ORCHESTRATOR)
    config_dir = REPO_ROOT / "config"
    monkeypatch.setenv("MODELS_CONFIG", str(config_dir / "models.yaml"))
    monkeypatch.setenv("OLLAMA_CATALOG_FALLBACK", str(config_dir / "ollama_catalog_fallback.json"))
    monkeypatch.setenv(
        "_LUMOGIS_TEST_SKIP_AUTH_CONSISTENCY_DO_NOT_SET_IN_PRODUCTION", "true"
    )
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("MCP_DEFAULT_USER_ID", "stdio-smoke-user")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    # Keep lifespan from starting inbox/ingest watchers or enqueueing batch jobs
    # against StdioTokenStore (incomplete fake — real paths would block startup).
    monkeypatch.setenv("LUMOGIS_INBOX_MODE", "off")
    monkeypatch.setenv("INGEST_PATHS_WATCH_MODE", "off")

    stub_orchestrator_lifespan_for_stdio(monkeypatch)

    import config

    as_of = datetime(2026, 6, 25, tzinfo=timezone.utc)
    monkeypatch.setattr(config, "get_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(
        config,
        "get_vector_store",
        lambda: FakeVS(
            [
                {
                    "id": "p",
                    "score": 0.9,
                    "payload": {
                        "memory_id": "m1",
                        "user_id": "stdio-smoke-user",
                        "bank": "coding",
                    },
                }
            ]
        ),
    )
    monkeypatch.setattr(config, "get_recall_reranker", lambda: None)
    monkeypatch.setattr(
        config,
        "get_metadata_store",
        lambda: FakeMS(
            [
                [{"id": "m1"}],
                [{"id": "m1"}],
                [
                    {
                        "id": "m1",
                        "content": "stdio smoke memory",
                        "valid_from": as_of,
                        "valid_until": None,
                    }
                ],
                [],
            ]
        ),
    )

    import services.mcp_write as mw

    monkeypatch.setattr(
        mw,
        "add_memory",
        lambda **k: {"memory_id": "smoke-mem", "entity_ids": [], "relation_ids": []},
    )

    token = _install_token_store(monkeypatch, scopes=["mcp:read", "mcp:write"])

    import main

    port = free_port()
    server = start_uvicorn(main.app, port)
    yield port, token
    stop_uvicorn(server)


def test_stdio_smoke_initialize_list_and_tool_calls(core_app):
    port, token = core_app
    env = os.environ.copy()
    env["LUMOGIS_MCP_URL"] = f"http://127.0.0.1:{port}/mcp/"
    env["LUMOGIS_MCP_TOKEN"] = token
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT / "clients" / "lumogis-mcp"), str(ORCHESTRATOR)]
    )

    init_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "lum292-smoke", "version": "0.1"},
        },
    }
    initialized = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    list_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    recall_req = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "recall",
            "arguments": {
                "query": "stdio",
                "bank": "coding",
                "retrieval_strategies": ["semantic", "bm25", "temporal"],
            },
        },
    }
    write_req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "add_memory",
            "arguments": {"content": "bridge write smoke", "bank": "coding"},
        },
    }

    responses = mcp_stdio_roundtrip(
        env, [init_req, initialized, list_req, recall_req, write_req]
    )
    assert len(responses) >= 4
    init_resp, list_resp, recall_resp, write_resp = (
        responses[0], responses[1], responses[2], responses[3]
    )

    assert "result" in init_resp, init_resp
    tool_names = {t["name"] for t in list_resp["result"]["tools"]}
    assert "recall" in tool_names
    assert "memory.search" in tool_names
    assert "add_memory" in tool_names

    recall_tool = next(t for t in list_resp["result"]["tools"] if t["name"] == "recall")
    assert recall_tool.get("annotations", {}).get("readOnlyHint") is True

    assert "isError" not in recall_resp.get("result", {}) or recall_resp["result"].get("isError") is False
    assert "memories" in json.dumps(recall_resp)

    assert "isError" not in write_resp.get("result", {}) or write_resp["result"].get("isError") is False
    assert "smoke-mem" in json.dumps(write_resp)
