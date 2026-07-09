# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route-level tests for the MCP token `scopes` mint field (LUM-527).

Kept separate from ``test_mcp_tokens_routes.py`` (whose assertions predate the
`scopes` request field) to avoid churn. Reuses that module's composite
``_RoutesFakeStore`` + ``_client`` so the wiring is identical; the ``store`` /
``dev_env`` fixtures are re-declared locally (they are tiny) so this file is
self-contained.
"""

from __future__ import annotations

import json

import pytest
from tests.test_mcp_tokens_routes import _client
from tests.test_mcp_tokens_routes import _RoutesFakeStore


@pytest.fixture
def store(monkeypatch):
    import config as _config
    from services import mcp_tokens as _mcp_tokens

    s = _RoutesFakeStore()
    _config._instances["metadata_store"] = s
    _mcp_tokens._LAST_STAMP_CACHE.clear()
    yield s
    _config._instances.pop("metadata_store", None)
    _mcp_tokens._LAST_STAMP_CACHE.clear()


@pytest.fixture
def dev_env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    yield


def _mint(client, body: dict):
    return client.post("/api/v1/me/mcp-tokens", json=body)


# --- accepted scope shapes ---------------------------------------------------


def test_mint_accepts_read_write_scopes(store, dev_env):
    with _client() as client:
        resp = _mint(client, {"label": "rw", "scopes": ["mcp:read", "mcp:write"]})
    assert resp.status_code == 201, resp.text
    assert resp.json()["token"]["scopes"] == ["mcp:read", "mcp:write"]
    # persisted on the row, too
    row = next(iter(store.tokens.values()))
    assert row["scopes"] == ["mcp:read", "mcp:write"]


def test_mint_accepts_read_only_scope(store, dev_env):
    with _client() as client:
        resp = _mint(client, {"label": "ro", "scopes": ["mcp:read"]})
    assert resp.status_code == 201, resp.text
    assert resp.json()["token"]["scopes"] == ["mcp:read"]


def test_mint_accepts_write_only_scope(store, dev_env):
    """A bare ["mcp:write"] is a valid non-empty subset (functionally read+write,
    since read tools are ungated); it round-trips unchanged (not normalised to
    include mcp:read)."""
    with _client() as client:
        resp = _mint(client, {"label": "wo", "scopes": ["mcp:write"]})
    assert resp.status_code == 201, resp.text
    assert resp.json()["token"]["scopes"] == ["mcp:write"]


def test_mint_omitted_scopes_defaults_to_read_only(store, dev_env):
    """LUM-531: body without `scopes` ⇒ least-privilege `["mcp:read"]` (NOT NULL).
    The route never mints an unrestricted token; write requires explicit mcp:write."""
    with _client() as client:
        resp = _mint(client, {"label": "default"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["token"]["scopes"] == ["mcp:read"]
    row = next(iter(store.tokens.values()))
    assert row["scopes"] == ["mcp:read"]


def test_mint_explicit_null_scopes_defaults_to_read_only(store, dev_env):
    """LUM-531: explicit `{"scopes": null}` ⇒ `["mcp:read"]` (same as omitted)."""
    with _client() as client:
        resp = _mint(client, {"label": "explicit-null", "scopes": None})
    assert resp.status_code == 201, resp.text
    assert resp.json()["token"]["scopes"] == ["mcp:read"]
    row = next(iter(store.tokens.values()))
    assert row["scopes"] == ["mcp:read"]  # persisted, not NULL


def test_mint_route_never_mints_null(store, dev_env):
    """LUM-531: neither omitted nor explicit-null produces a NULL-scope row —
    the route always sends a concrete least-privilege list to the service."""
    with _client() as client:
        assert _mint(client, {"label": "a"}).status_code == 201
        assert _mint(client, {"label": "b", "scopes": None}).status_code == 201
    assert all(row["scopes"] == ["mcp:read"] for row in store.tokens.values())
    assert not any(row["scopes"] is None for row in store.tokens.values())


def test_mint_dedupes_scopes_in_canonical_order(store, dev_env):
    with _client() as client:
        resp = _mint(client, {"label": "dup", "scopes": ["mcp:write", "mcp:read", "mcp:read"]})
    assert resp.status_code == 201, resp.text
    # de-duped AND re-ordered to canonical KNOWN_MCP_SCOPES order
    assert resp.json()["token"]["scopes"] == ["mcp:read", "mcp:write"]


# --- rejected scope shapes ---------------------------------------------------


def test_mint_rejects_unknown_scope(store, dev_env):
    with _client() as client:
        resp = _mint(client, {"label": "bad", "scopes": ["mcp:admin"]})
    assert resp.status_code == 422, resp.text
    assert store.tokens == {}  # nothing minted


def test_mint_rejects_empty_scope_list(store, dev_env):
    with _client() as client:
        resp = _mint(client, {"label": "empty", "scopes": []})
    assert resp.status_code == 422, resp.text
    assert store.tokens == {}


# --- audit -------------------------------------------------------------------


def test_mint_audit_includes_scopes(store, dev_env):
    with _client() as client:
        _mint(client, {"label": "audited", "scopes": ["mcp:read"]})
    minted = [a for a in store.audit if a["action_name"] == "__mcp_token__.minted"]
    assert minted, "expected a __mcp_token__.minted audit row"
    summary = json.loads(minted[-1]["input_summary"])
    assert summary["scopes"] == ["mcp:read"]
    assert summary["label"] == "audited"


def test_mint_audit_default_scopes_is_read_only(store, dev_env):
    """LUM-531: an omitted-scopes mint audits `["mcp:read"]`, NOT `null` — the
    operator must see the actual granted scope, not a misleading unrestricted."""
    with _client() as client:
        _mint(client, {"label": "default-audit"})
    minted = [a for a in store.audit if a["action_name"] == "__mcp_token__.minted"]
    summary = json.loads(minted[-1]["input_summary"])
    assert summary["scopes"] == ["mcp:read"]
