# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Contract + e2e tests for the `recall` MCP tool (LUM-295).

- Manifest: `recall` is advertised with its input/output schema + read-only
  annotations.
- Transport: `recall` is callable over `/mcp/` JSON-RPC and round-trips
  `{"memories": [...]}`.
- Archive observability (the load-bearing proof): a memory archived via
  `forget`/supersede (LUM-526) — i.e. not returned by the temporal-filtered
  hydration — is absent from recall, while a valid sibling is present.
"""

import json
from datetime import datetime
from datetime import timezone

from fastapi.testclient import TestClient

from services import recall as R

AS_OF = datetime(2026, 6, 25, tzinfo=timezone.utc)


class FakeMS:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def ping(self) -> bool:
        return True

    def fetch_one(self, query, params=None):
        # Satisfy lifespan bootstrap/auth gates without a live Postgres.
        if "COUNT" in query.upper():
            return {"n": 1}
        return None

    def fetch_all(self, query, params=None):
        self.calls.append((query, params))
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


# --- manifest contract ------------------------------------------------------


def test_recall_in_core_manifest_with_schema_and_read_only_annotation():
    import mcp_server

    manifest = mcp_server.build_core_manifest()
    tool = next((t for t in manifest.tools if t.name == "recall"), None)
    assert tool is not None
    assert tool.input_schema["required"] == ["query"]
    strategies = tool.input_schema["properties"]["retrieval_strategies"]["items"]["enum"]
    assert set(strategies) == {"semantic", "bm25", "graph", "temporal"}
    assert "memories" in tool.output_schema["properties"]
    # read-only annotation present (auto-approvable; not destructive)
    ann = mcp_server._read_only_annotations("Recall memories")
    if ann is not None:  # None only when the MCP SDK is absent
        assert ann.readOnlyHint is True and ann.destructiveHint is False


# --- transport round-trip ---------------------------------------------------


def _post(client, payload, headers):
    base = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Host": "localhost:8000",
    }
    base.update(headers)
    return client.post("/mcp/", content=json.dumps(payload), headers=base)


def test_recall_callable_end_to_end(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "e2e-secret")
    monkeypatch.setenv("MCP_DEFAULT_USER_ID", "e2e-user")

    # Wire fakes so the real recall service runs through the transport without
    # a live Qdrant/Postgres/Ollama. semantic+bm25 surface m1; hydration returns it.
    import config

    monkeypatch.setattr(config, "get_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(
        config,
        "get_vector_store",
        lambda: FakeVS(
            [
                {
                    "id": "p",
                    "score": 0.9,
                    "payload": {"memory_id": "m1", "user_id": "e2e-user", "bank": "coding"},
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
                [{"id": "m1"}],  # bm25
                [{"id": "m1"}],  # temporal
                [
                    {
                        "id": "m1",
                        "content": "FalkorDB chosen over Neo4j",
                        "bank": "coding",
                        "valid_from": AS_OF,
                        "valid_until": None,
                    }
                ],
                [],  # hydrate edges
            ]
        ),
    )

    import main

    headers = {"Authorization": "Bearer e2e-secret"}
    with TestClient(main.app) as client:
        init = _post(
            client,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "lum295", "version": "0.1"},
                },
            },
            headers,
        )
        assert init.status_code == 200, init.text
        call = _post(
            client,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "recall",
                    "arguments": {
                        "query": "FalkorDB",
                        "bank": "coding",
                        "retrieval_strategies": ["semantic", "bm25", "temporal"],
                    },
                },
            },
            headers,
        )
    assert call.status_code == 200, call.text
    assert "memories" in call.text
    assert "m1" in call.text
    assert 'isError":true' not in call.text.replace(" ", "")


# --- archive observability (load-bearing) -----------------------------------


def test_recall_when_memory_archived_then_absent_but_valid_sibling_present():
    """An archived memory (dropped by the temporal hydration filter) does not
    surface; a currently-valid sibling does. This is what makes LUM-526's
    forget/supersede observable on the read path."""
    vs = FakeVS(
        [
            {
                "id": "pA",
                "score": 0.9,
                "payload": {"memory_id": "archived", "user_id": "u", "bank": "coding"},
            },
            {
                "id": "pB",
                "score": 0.8,
                "payload": {"memory_id": "valid", "user_id": "u", "bank": "coding"},
            },
        ]
    )
    # bm25 + temporal surface both ids; temporal-filtered hydration returns only the valid one.
    ms = FakeMS(
        [
            [{"id": "archived"}, {"id": "valid"}],  # bm25
            [{"id": "archived"}, {"id": "valid"}],  # temporal
            [
                {
                    "id": "valid",
                    "content": "current",
                    "bank": "coding",
                    "valid_from": AS_OF,
                    "valid_until": None,
                }
            ],  # hydrate q1 (archived absent)
            [],  # hydrate edges
        ]
    )
    res = R.recall(
        user_id="u",
        bank="coding",
        query="x",
        retrieval_strategies=["semantic", "bm25", "temporal"],
        as_of=AS_OF,
        rerank=False,
        ms=ms,
        embedder=FakeEmbedder(),
        vs=vs,
    )
    ids = [m.id for m in res]
    assert "valid" in ids and "archived" not in ids


# --- recall_tool as_of parsing (VERIFY-PLAN: P2/P3 fixes) --------------------


def test_recall_tool_rejects_malformed_as_of(monkeypatch):
    import mcp_server

    monkeypatch.setattr(mcp_server, "_resolve_user_id", lambda: "u")
    try:
        mcp_server.recall_tool(query="x", as_of="not-a-timestamp")
        assert False, "expected ValueError on malformed as_of"
    except ValueError as exc:
        assert "as_of" in str(exc)


def test_recall_tool_coerces_naive_as_of_to_utc(monkeypatch):
    from datetime import timezone

    import mcp_server

    monkeypatch.setattr(mcp_server, "_resolve_user_id", lambda: "u")
    captured = {}

    def _fake_recall(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("services.recall.recall", _fake_recall)
    mcp_server.recall_tool(query="x", as_of="2026-06-25T12:00:00")  # naive ISO
    assert captured["as_of"].tzinfo == timezone.utc
