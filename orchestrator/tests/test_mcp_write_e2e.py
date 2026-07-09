# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""End-to-end test: add_memory is callable over /mcp/ JSON-RPC (LUM-291).

Exercises the full path — FastMCP tool registration, the auth gate, scope
resolution (legacy unrestricted token), _resolve_user_id, and the write
service — through the real Starlette mount, not a direct function call.
Entity/relation extraction is stubbed to avoid a live Ollama call.
"""

import json

from fastapi.testclient import TestClient


def _initialize_payload() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "lum291-e2e", "version": "0.1"},
        },
    }


def _post(client, payload, headers):
    base = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Host": "localhost:8000",
    }
    base.update(headers)
    return client.post("/mcp/", content=json.dumps(payload), headers=base)


def test_add_memory_callable_end_to_end(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "e2e-secret")
    monkeypatch.setenv("MCP_DEFAULT_USER_ID", "e2e-user")
    # Stub extraction so the tool does not reach a live LLM.
    import services.mcp_write as mw

    monkeypatch.setattr(mw, "extract_entities", lambda *a, **k: [])
    monkeypatch.setattr(mw, "extract_relations", lambda *a, **k: [])

    import main

    headers = {"Authorization": "Bearer e2e-secret"}
    with TestClient(main.app) as client:
        init = _post(client, _initialize_payload(), headers)
        assert init.status_code == 200, init.text
        call = _post(
            client,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "add_memory",
                    "arguments": {"content": "FalkorDB chosen over Neo4j", "bank": "coding"},
                },
            },
            headers,
        )
    assert call.status_code == 200, call.text
    # The tool's structured result (memory_id) round-trips through JSON-RPC.
    assert "memory_id" in call.text
    assert 'isError":true' not in call.text.replace(" ", "")


# ---------------------------------------------------------------------------
# LUM-527 — scope enforcement end-to-end with a real minted `lmcp_` token.
#
# The test above authenticates with the static `MCP_AUTH_TOKEN` (scopes=None,
# unrestricted) and therefore CANNOT exercise scope enforcement. These mint a
# real `lmcp_` token whose `scopes` flow through `mcp_tokens.verify()` →
# `request.state.mcp_scopes` → the `_current_mcp_scopes` ContextVar →
# `_require_scope`. A composite fake store (reused from the route tests) backs
# `verify()`; `add_memory` is stubbed so the assertion isolates the scope GATE
# (which runs in the tool BEFORE the writer), not the write chain.
# ---------------------------------------------------------------------------


def _install_token_store(monkeypatch, *, scopes):
    """Install a fake metadata store, mint an `lmcp_` token with `scopes`, and
    return its plaintext bearer. The store backs `mcp_tokens.verify()`."""
    from tests.test_mcp_tokens_routes import _RoutesFakeStore

    import config as _config
    from services import mcp_tokens as _mcp_tokens

    s = _RoutesFakeStore()
    _config._instances["metadata_store"] = s
    _mcp_tokens._LAST_STAMP_CACHE.clear()
    monkeypatch.setattr(_config, "get_metadata_store", lambda: _config._instances["metadata_store"])
    _row, plaintext = _mcp_tokens.mint("scope-e2e-user", "e2e", scopes=scopes)
    return plaintext


def test_read_scoped_token_denied_on_write_tool_e2e(monkeypatch):
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    import services.mcp_write as mw

    called = {"n": 0}
    monkeypatch.setattr(mw, "add_memory", lambda **k: called.__setitem__("n", called["n"] + 1))

    plaintext = _install_token_store(monkeypatch, scopes=["mcp:read"])
    import main

    headers = {"Authorization": f"Bearer {plaintext}"}
    with TestClient(main.app) as client:
        init = _post(client, _initialize_payload(), headers)
        assert init.status_code == 200, init.text
        call = _post(
            client,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "add_memory",
                    "arguments": {"content": "should be denied", "bank": "coding"},
                },
            },
            headers,
        )
    assert call.status_code == 200, call.text
    # Scope gate fired → JSON-RPC tool error, and the writer was never reached.
    assert 'isError":true' in call.text.replace(" ", "")
    assert "mcp:write" in call.text
    assert called["n"] == 0


def test_write_scoped_token_allowed_on_write_tool_e2e(monkeypatch):
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    import services.mcp_write as mw

    monkeypatch.setattr(
        mw,
        "add_memory",
        lambda **k: {"memory_id": "e2e-ok", "entity_ids": [], "relation_ids": []},
    )

    plaintext = _install_token_store(monkeypatch, scopes=["mcp:read", "mcp:write"])
    import main

    headers = {"Authorization": f"Bearer {plaintext}"}
    with TestClient(main.app) as client:
        init = _post(client, _initialize_payload(), headers)
        assert init.status_code == 200, init.text
        call = _post(
            client,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "add_memory",
                    "arguments": {"content": "write allowed", "bank": "coding"},
                },
            },
            headers,
        )
    assert call.status_code == 200, call.text
    # Write scope present → gate passed → writer reached → memory_id returned.
    assert "e2e-ok" in call.text
    assert 'isError":true' not in call.text.replace(" ", "")


def test_omitted_scopes_token_denied_on_write_tool_e2e(monkeypatch):
    """LUM-531: a token minted via the ROUTE with omitted `scopes` is read-only
    END-TO-END — it defaults to ["mcp:read"] and is denied on a write tool.

    Mints through the real route (no auth headers under AUTH_ENABLED=false) so
    this exercises the route's default-least-privilege mapping, then drives /mcp/
    with the returned lmcp_ bearer.
    """
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)

    from tests.test_mcp_tokens_routes import _RoutesFakeStore

    import config as _config
    from services import mcp_tokens as _mcp_tokens

    s = _RoutesFakeStore()
    _config._instances["metadata_store"] = s
    _mcp_tokens._LAST_STAMP_CACHE.clear()
    monkeypatch.setattr(_config, "get_metadata_store", lambda: _config._instances["metadata_store"])

    import services.mcp_write as mw

    called = {"n": 0}

    def _should_not_run(**k):
        called["n"] += 1
        return {"memory_id": "should-not-reach", "entity_ids": [], "relation_ids": []}

    monkeypatch.setattr(mw, "add_memory", _should_not_run)

    import main

    with TestClient(main.app) as client:
        # Mint via the route, omitting scopes → must default to read-only.
        mint = client.post("/api/v1/me/mcp-tokens", json={"label": "e2e-default"})
        assert mint.status_code == 201, mint.text
        assert mint.json()["token"]["scopes"] == ["mcp:read"]
        plaintext = mint.json()["plaintext"]

        headers = {"Authorization": f"Bearer {plaintext}"}
        init = _post(client, _initialize_payload(), headers)
        assert init.status_code == 200, init.text
        call = _post(
            client,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "add_memory",
                    "arguments": {"content": "omitted-scopes must be denied", "bank": "coding"},
                },
            },
            headers,
        )
    assert call.status_code == 200, call.text
    # Read-only default → write gate fires → JSON-RPC error; writer never reached.
    assert 'isError":true' in call.text.replace(" ", "")
    assert "mcp:write" in call.text
    assert called["n"] == 0
