# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for the Area-4 MCP server surface.

Covers:
  * build_core_manifest() returns a valid CapabilityManifest with the 5
    community tools.
  * GET /capabilities returns the manifest and round-trips through the
    Pydantic schema.
  * Each of the 5 MCP tool functions returns the documented shape, with
    the underlying service mocked.
  * GET / exposes mcp_enabled / mcp_auth_required without breaking any
    pre-existing field.
  * MCP_AUTH_TOKEN bearer middleware: blocks missing/wrong tokens, lets
    correct tokens (and the unset case) through.
  * /mcp mount survives FastAPI lifespan startup (smoke test).
  * Graceful degradation when mcp_server.mcp is None (SDK unavailable).
"""

import json

import pytest
from fastapi.testclient import TestClient

import config as _config
from models.capability import CapabilityManifest
from models.memory import ContextHit
from models.memory import SessionSummary


# ---------------------------------------------------------------------------
# Manifest contract
# ---------------------------------------------------------------------------


def test_build_core_manifest_round_trips_through_pydantic():
    import mcp_server

    manifest = mcp_server.build_core_manifest()
    assert isinstance(manifest, CapabilityManifest)
    assert manifest.id == "lumogis.core"
    assert manifest.transport.value == "mcp"
    # 5 community tools, exact set, in order
    names = [t.name for t in manifest.tools]
    assert names == [
        "memory.search",
        "memory.get_recent",
        "entity.lookup",
        "entity.search",
        "context.build",
    ]
    # Round-trip through JSON (proves model_dump and validators agree)
    reparsed = CapabilityManifest.model_validate_json(manifest.model_dump_json())
    assert reparsed == manifest


def test_capabilities_route_returns_valid_manifest_json():
    import main

    with TestClient(main.app) as client:
        resp = client.get("/capabilities")
    assert resp.status_code == 200
    body = resp.json()
    # JSON-mode dump serialises enums to their string values
    assert body["id"] == "lumogis.core"
    assert body["transport"] == "mcp"
    assert body["license_mode"] == "community"
    # Pydantic re-validates the wire format end-to-end
    CapabilityManifest.model_validate(body)


# ---------------------------------------------------------------------------
# Tool implementations — call the Python functions directly with the
# underlying services mocked. We exercise the real wrappers, not the MCP
# transport, because the transport is the SDK's responsibility.
# ---------------------------------------------------------------------------


def test_memory_search_tool_wraps_retrieve_context(monkeypatch):
    import mcp_server

    monkeypatch.setenv("MCP_DEFAULT_USER_ID", "test-user")
    fake_hits = [
        ContextHit(session_id="s1", summary="hello", score=0.91, scope="shared"),
    ]
    monkeypatch.setattr(
        "services.memory.retrieve_context",
        lambda query, limit, user_id: fake_hits,
    )
    out = mcp_server.memory_search(query="anything", limit=3)
    assert out == {
        "results": [
            {
                "session_id": "s1",
                "summary": "hello",
                "score": 0.91,
                "scope": "shared",
            },
        ],
    }


def test_memory_get_recent_tool_wraps_recent_sessions(monkeypatch):
    import mcp_server

    monkeypatch.setenv("MCP_DEFAULT_USER_ID", "test-user")
    monkeypatch.setattr(
        "services.memory.recent_sessions",
        lambda limit, user_id: [
            SessionSummary(
                session_id="s1",
                summary="x",
                topics=["a"],
                entities=["E"],
                scope="system",
            ),
        ],
    )
    out = mcp_server.memory_get_recent(limit=5)
    assert out == {
        "sessions": [
            {
                "session_id": "s1",
                "summary": "x",
                "topics": ["a"],
                "entities": ["E"],
                "scope": "system",
            },
        ],
    }


def test_entity_lookup_tool_wraps_lookup_by_name(monkeypatch):
    import mcp_server

    monkeypatch.setenv("MCP_DEFAULT_USER_ID", "test-user")
    fake = {
        "name": "Ada",
        "entity_type": "PERSON",
        "mention_count": 1,
        "aliases": [],
        "context_tags": [],
        "scope": "personal",
    }
    monkeypatch.setattr(
        "services.entities.lookup_by_name",
        lambda name, user_id: fake,
    )
    assert mcp_server.entity_lookup(name="Ada") == {"entity": fake}


def test_entity_lookup_tool_returns_none_entity_when_not_found(monkeypatch):
    import mcp_server

    monkeypatch.setenv("MCP_DEFAULT_USER_ID", "test-user")
    monkeypatch.setattr(
        "services.entities.lookup_by_name",
        lambda name, user_id: None,
    )
    assert mcp_server.entity_lookup(name="Nobody") == {"entity": None}


def test_entity_search_tool_wraps_search_by_name(monkeypatch):
    import mcp_server

    monkeypatch.setenv("MCP_DEFAULT_USER_ID", "test-user")
    rows = [
        {
            "name": "Lumogis",
            "entity_type": "PROJECT",
            "mention_count": 4,
            "aliases": [],
            "context_tags": [],
            "scope": "shared",
        }
    ]
    monkeypatch.setattr(
        "services.entities.search_by_name",
        lambda query, limit, user_id: rows,
    )
    assert mcp_server.entity_search(query="lum", limit=10) == {"entities": rows}


def test_context_build_combines_search_and_memory(monkeypatch):
    import mcp_server

    monkeypatch.setenv("MCP_DEFAULT_USER_ID", "test-user")

    class _DocHit:
        def __init__(self, text, source):
            self.text = text
            self.source = source

    monkeypatch.setattr(
        "services.search.semantic_search",
        lambda query, limit, user_id: [
            _DocHit("alpha", "doc-1"),
            _DocHit("beta", "doc-2"),
        ],
    )
    monkeypatch.setattr(
        "services.memory.retrieve_context",
        lambda query, limit, user_id: [
            ContextHit(session_id="s1", summary="gamma", score=0.5),
        ],
    )
    # Avoid pulling tiktoken / token-budget machinery into the assertion
    monkeypatch.setattr(
        "services.context_budget.truncate_text",
        lambda text, max_tokens: text,
    )
    out = mcp_server.context_build(query="x", max_tokens=2000)
    assert "alpha" in out["context"]
    assert "beta" in out["context"]
    assert "[session s1] gamma" in out["context"]
    assert out["sources"] == ["doc-1", "doc-2", "session:s1"]


def test_context_build_recovers_from_underlying_failures(monkeypatch):
    """semantic_search and retrieve_context failures must not raise."""
    import mcp_server

    monkeypatch.setenv("MCP_DEFAULT_USER_ID", "test-user")

    def boom(*a, **k):
        raise RuntimeError("backend down")

    monkeypatch.setattr("services.search.semantic_search", boom)
    monkeypatch.setattr("services.memory.retrieve_context", boom)
    monkeypatch.setattr(
        "services.context_budget.truncate_text",
        lambda text, max_tokens: text,
    )
    out = mcp_server.context_build(query="x", max_tokens=2000)
    assert out == {"context": "", "sources": []}


# ---------------------------------------------------------------------------
# GET / status_page — mcp_enabled / mcp_auth_required additions
# ---------------------------------------------------------------------------


def test_status_page_reports_mcp_enabled_when_sdk_present():
    import main

    with TestClient(main.app) as client:
        resp = client.get("/")
    body = resp.json()
    # mcp package is installed in the test env (declared in requirements-test.txt)
    assert body["mcp_enabled"] is True


def test_status_page_reports_mcp_auth_required_from_env(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "secret-token")
    import main

    with TestClient(main.app) as client:
        resp = client.get("/")
    body = resp.json()
    assert body["mcp_auth_required"] is True


def test_status_page_mcp_auth_required_false_when_unset(monkeypatch):
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    import main

    with TestClient(main.app) as client:
        resp = client.get("/")
    body = resp.json()
    assert body["mcp_auth_required"] is False


def test_status_page_pre_existing_fields_unchanged():
    """Regression: every pre-existing GET / field is still present."""
    import main

    with TestClient(main.app) as client:
        resp = client.get("/")
    body = resp.json()
    for required in (
        "status",
        "embedding_model_ready",
        "documents_indexed",
        "sessions_stored",
        "entities_known",
        "services",
        "capability_services",
        "links",
        "setup_needed",
    ):
        assert required in body, f"missing pre-existing field: {required}"


# ---------------------------------------------------------------------------
# /mcp mount and MCP_AUTH_TOKEN middleware
# ---------------------------------------------------------------------------


def _mcp_initialize_payload() -> dict:
    """Minimal MCP `initialize` JSON-RPC request — the very first call any
    MCP client makes to a server. We use this as a smoke test because
    every conformant MCP server must respond to it."""
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "lumogis-test-client", "version": "0.1"},
        },
    }


def _mcp_post(client, payload, headers=None):
    base_headers = {
        "Content-Type": "application/json",
        # Streamable-HTTP requires the client to advertise it accepts both.
        "Accept": "application/json, text/event-stream",
        # FastMCP enables DNS-rebinding protection by default and only
        # allows localhost-family hosts. TestClient defaults Host to
        # `testserver` which gets rejected with 421 — override it here.
        "Host": "localhost:8000",
    }
    if headers:
        base_headers.update(headers)
    # Canonical endpoint is /mcp/ (with trailing slash) — see dashboard URL.
    # POSTing to /mcp triggers a 307 redirect that TestClient/httpx treats
    # as cross-origin (testserver vs the localhost Host we set above) and
    # therefore strips the Authorization header on the redirect, which
    # would mask real auth bugs in this test.
    return client.post("/mcp/", content=json.dumps(payload), headers=base_headers)


def test_mcp_endpoint_responds_to_initialize_when_no_token_set(monkeypatch):
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    import main

    with TestClient(main.app) as client:
        resp = _mcp_post(client, _mcp_initialize_payload())
    # Stateless JSON mode: should be a 200 with a JSON-RPC envelope, not 404
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("jsonrpc") == "2.0"
    assert "result" in body, body


def test_mcp_endpoint_blocks_missing_token_when_token_required(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "topsecret")
    import main

    with TestClient(main.app) as client:
        resp = _mcp_post(client, _mcp_initialize_payload())
    assert resp.status_code == 401
    assert resp.json() == {"error": "invalid mcp token"}


def test_mcp_endpoint_blocks_wrong_token(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "topsecret")
    import main

    with TestClient(main.app) as client:
        resp = _mcp_post(
            client,
            _mcp_initialize_payload(),
            headers={"Authorization": "Bearer wrong"},
        )
    assert resp.status_code == 401


def test_mcp_endpoint_accepts_correct_token(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "topsecret")
    import main

    with TestClient(main.app) as client:
        resp = _mcp_post(
            client,
            _mcp_initialize_payload(),
            headers={"Authorization": "Bearer topsecret"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("jsonrpc") == "2.0"
    assert "result" in body


def test_capabilities_endpoint_not_gated_by_mcp_token(monkeypatch):
    """GET /capabilities is the public discovery contract — never gated."""
    monkeypatch.setenv("MCP_AUTH_TOKEN", "topsecret")
    import main

    with TestClient(main.app) as client:
        resp = client.get("/capabilities")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Graceful degradation when the mcp SDK is unavailable
# ---------------------------------------------------------------------------


def test_status_page_reports_mcp_disabled_when_sdk_missing(monkeypatch):
    """If `mcp_server.mcp` is None (SDK absent), GET / still works and
    mcp_enabled is False — Core does not crash, /capabilities still serves."""
    import mcp_server

    monkeypatch.setattr(mcp_server, "mcp", None)
    import main

    with TestClient(main.app) as client:
        status_resp = client.get("/")
        cap_resp = client.get("/capabilities")
    assert status_resp.status_code == 200
    assert status_resp.json()["mcp_enabled"] is False
    # Manifest is still served from the hand-coded MCP_TOOLS_FOR_MANIFEST list
    assert cap_resp.status_code == 200
    assert cap_resp.json()["id"] == "lumogis.core"
