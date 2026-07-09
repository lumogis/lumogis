# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Scope-enforcement tests for the MCP write surface (LUM-291)."""

import mcp_server
import pytest


def test_require_scope_none_is_unrestricted():
    tok = mcp_server._set_current_mcp_scopes(None)
    try:
        mcp_server._require_scope("mcp:write")  # must not raise
    finally:
        mcp_server._reset_current_mcp_scopes(tok)


def test_require_scope_present_allows():
    tok = mcp_server._set_current_mcp_scopes(["mcp:write"])
    try:
        mcp_server._require_scope("mcp:write")
    finally:
        mcp_server._reset_current_mcp_scopes(tok)


def test_require_scope_missing_denies():
    tok = mcp_server._set_current_mcp_scopes(["mcp:read"])
    try:
        with pytest.raises(mcp_server.McpScopeError):
            mcp_server._require_scope("mcp:write")
    finally:
        mcp_server._reset_current_mcp_scopes(tok)


def test_add_memory_tool_denied_without_write_scope(monkeypatch):
    """A read-scoped token is denied AND the writer is never invoked."""
    import services.mcp_write as mw

    called = {"n": 0}
    monkeypatch.setattr(mw, "add_memory", lambda **k: called.__setitem__("n", called["n"] + 1))

    tok = mcp_server._set_current_mcp_scopes(["mcp:read"])
    try:
        with pytest.raises(mcp_server.McpScopeError):
            mcp_server.add_memory_tool(content="hi")
    finally:
        mcp_server._reset_current_mcp_scopes(tok)
    assert called["n"] == 0


def test_add_memory_tool_allowed_with_write_scope(monkeypatch):
    import services.mcp_write as mw

    monkeypatch.setattr(
        mw, "add_memory",
        lambda **k: {"memory_id": "m", "entity_ids": [], "relation_ids": []},
    )
    monkeypatch.setattr(mcp_server, "_resolve_user_id", lambda: "u")

    tok = mcp_server._set_current_mcp_scopes(["mcp:write"])
    try:
        out = mcp_server.add_memory_tool(content="hi", bank="coding")
    finally:
        mcp_server._reset_current_mcp_scopes(tok)
    assert out["memory_id"] == "m"


def test_mint_supports_scopes(monkeypatch):
    """mint() now accepts scopes so a write-scoped token can be created."""
    from datetime import datetime
    from datetime import timezone
    from unittest.mock import Mock

    import config
    from services import mcp_tokens

    inserted = {}
    ms = Mock()

    def _exec(sql, params=None):
        if "INSERT INTO mcp_tokens" in sql:
            inserted["params"] = params

    ms.execute.side_effect = _exec
    ms.fetch_one.return_value = {
        "id": "t1", "user_id": "u", "token_prefix": "lmcp_aaaaaaaaaaaa",
        "token_hash": "h", "label": "x", "scopes": ["mcp:write"],
        "created_at": datetime.now(timezone.utc), "last_used_at": None,
        "revoked_at": None, "expires_at": None,
    }
    monkeypatch.setattr(config, "get_metadata_store", lambda: ms)

    row, plaintext = mcp_tokens.mint("u", "x", scopes=["mcp:write"])
    assert plaintext.startswith("lmcp_")
    # the scopes value reached the INSERT params (last positional)
    assert inserted["params"][-1] == ["mcp:write"]
    assert row.scopes == ["mcp:write"]
