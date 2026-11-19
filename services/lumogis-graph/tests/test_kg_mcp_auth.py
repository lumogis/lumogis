# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-915 — graph ``/mcp`` is gated and every tool runs as the verified caller.

End to end over the real ``main.app`` mount (lifespan started): JSON-RPC
``initialize`` + ``tools/call`` through ``auth.auth_middleware`` and the
stateless FastMCP session manager. The first test is the proof that the
ContextVar set in the middleware reaches the tool.

Every request sends ``Host: localhost:8000``: the MCP SDK answers 421 to any
other ``Host`` (DNS-rebinding guard), which is not a ContextVar failure.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time

import jwt
import pytest

SECRET = "kg-test-access-secret-0123456789abcdef"
_LOCAL_HOST = "localhost:8000"


def _token(sub="alice", role="user", typ="access", tv=0, **extra) -> str:
    now = int(time.time())
    payload = {"sub": sub, "role": role, "typ": typ, "iat": now, "exp": now + 300, **extra}
    if tv is not None:
        payload["tv"] = tv
    return jwt.encode(payload, SECRET, algorithm="HS256")


# Copied from orchestrator/tests/test_mcp_write_e2e.py (graph tests cannot import Core's).
def _initialize_payload() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "lum915-e2e", "version": "0.1"},
        },
    }


def _post(client, payload, headers=None, host=_LOCAL_HOST, path="/mcp/"):
    base = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Host": host,
    }
    base.update(headers or {})
    return client.post(path, content=json.dumps(payload), headers=base)


def _call(client, name, arguments, headers=None, host=_LOCAL_HOST):
    init = _post(client, _initialize_payload(), headers, host=host)
    if init.status_code != 200:
        return init
    return _post(
        client,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers,
        host=host,
    )


def _bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def _is_error(resp) -> bool:
    return bool(resp.json()["result"].get("isError"))


def _text(resp) -> str:
    return json.dumps(resp.json()["result"])


@pytest.fixture
def auth_on(monkeypatch, mock_metadata_store):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", SECRET)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("GRAPH_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("LUMOGIS_REQUIRE_TV_CLAIM", raising=False)
    mock_metadata_store.token_versions = {"alice": 0, "admin-1": 0}


@pytest.fixture
def auth_off(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("GRAPH_ADMIN_TOKEN", raising=False)


@pytest.fixture
def recorder(monkeypatch):
    """Record the user_id each graph helper is called with."""
    import graph.query as gq

    seen: list[str] = []

    def _query(spec, *, user_id):
        seen.append(user_id)
        return "ok"

    def _context(*, query, context_fragments, user_id="default", **_kw):
        seen.append(user_id)

    monkeypatch.setattr(gq, "query_graph_tool", _query)
    monkeypatch.setattr(gq, "on_context_building", _context)
    return seen


@pytest.fixture
def reconcile(monkeypatch):
    """Replace run_reconciliation; record calls and whether each ran off the loop."""
    import graph.reconcile as gr

    calls: list[dict] = []

    def _run(limit_per_type=None):
        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:
            on_loop = False
        calls.append({"limit": limit_per_type, "on_loop": on_loop})
        return {"totals": {"scanned": 0}}

    monkeypatch.setattr(gr, "run_reconciliation", _run)
    return calls


@pytest.fixture(autouse=True)
def _backfill_flag_clear():
    from graph import routes

    routes._backfill_running.clear()
    yield
    routes._backfill_running.clear()


_READ_TOOLS = [
    ("graph.query_ego", {"entity": "Ada"}),
    ("graph.query_path", {"from_entity": "Ada", "to_entity": "Bob"}),
    ("graph.query_mentions", {"entity": "Ada"}),
    ("graph.get_context", {"query": "Ada"}),
]


# ---------------------------------------------------------------------------
# Caller binding (the ContextVar proof)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("tool", "args"), _READ_TOOLS)
def test_mcp_tool_runs_as_jwt_sub(app_with_lifespan, auth_on, recorder, tool, args):
    with app_with_lifespan as client:
        resp = _call(client, tool, args, _bearer(_token()))
    assert resp.status_code == 200, resp.text
    assert not _is_error(resp), resp.text
    assert recorder == ["alice"]


def test_mcp_accepts_token_without_tv_when_claim_not_required(
    app_with_lifespan, auth_on, recorder, monkeypatch
):
    monkeypatch.setenv("LUMOGIS_REQUIRE_TV_CLAIM", "false")
    with app_with_lifespan as client:
        resp = _call(client, "graph.query_ego", {"entity": "Ada"}, _bearer(_token(tv=None)))
    assert resp.status_code == 200 and not _is_error(resp), resp.text
    assert recorder == ["alice"]


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_mcp_missing_bearer_401_when_auth_on(app_with_lifespan, auth_on, recorder):
    with app_with_lifespan as client:
        resp = _call(client, "graph.query_ego", {"entity": "Ada"})
    assert resp.status_code == 401
    assert resp.json() == {"error": "missing token"}
    assert recorder == []


def test_mcp_without_trailing_slash_is_gated(app_with_lifespan, auth_on):
    with app_with_lifespan as client:
        resp = _post(client, _initialize_payload(), path="/mcp")
    assert resp.status_code == 401


def test_mcp_stale_tv_401(app_with_lifespan, auth_on, mock_metadata_store, recorder):
    mock_metadata_store.token_versions["alice"] = 2
    with app_with_lifespan as client:
        resp = _call(client, "graph.query_ego", {"entity": "Ada"}, _bearer(_token(tv=1)))
    assert resp.status_code == 401
    assert recorder == []


def test_mcp_refresh_typed_jwt_401(app_with_lifespan, auth_on, recorder):
    with app_with_lifespan as client:
        resp = _call(client, "graph.query_ego", {"entity": "Ada"}, _bearer(_token(typ="refresh")))
    assert resp.status_code == 401
    assert recorder == []


def test_mcp_tv_lookup_error_is_503(app_with_lifespan, auth_on, mock_metadata_store, recorder):
    def _boom(query, params=None):
        raise RuntimeError("postgres down")

    mock_metadata_store.fetch_one = _boom
    with app_with_lifespan as client:
        resp = _post(client, _initialize_payload(), _bearer(_token()))
    assert resp.status_code == 503
    assert resp.json() == {"error": "auth check unavailable"}


def test_mcp_auth_token_refused_when_auth_on(app_with_lifespan, auth_on, monkeypatch, recorder):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "s3cret")
    with app_with_lifespan as client:
        resp = _call(client, "graph.query_ego", {"entity": "Ada"}, _bearer("s3cret"))
    assert resp.status_code == 401
    assert "AUTH_ENABLED=true" in resp.json()["error"]
    assert recorder == []


def test_mcp_auth_token_required_when_set_and_auth_off(
    app_with_lifespan, auth_off, monkeypatch, recorder
):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "s3cret")
    with app_with_lifespan as client:
        assert _call(client, "graph.query_ego", {"entity": "Ada"}).status_code == 401
        assert (
            _call(client, "graph.query_ego", {"entity": "Ada"}, _bearer("wrong")).status_code == 401
        )
        ok = _call(client, "graph.query_ego", {"entity": "Ada"}, _bearer("s3cret"))
    assert ok.status_code == 200 and not _is_error(ok), ok.text
    assert recorder == ["default"]


def test_mcp_open_as_default_when_auth_off_and_no_token(app_with_lifespan, auth_off, recorder):
    with app_with_lifespan as client:
        resp = _call(client, "graph.query_ego", {"entity": "Ada"})
    assert resp.status_code == 200 and not _is_error(resp), resp.text
    assert recorder == ["default"]


@pytest.mark.parametrize("auth_mode", ["on", "off"])
def test_mcp_non_ascii_bearer_is_401(app_with_lifespan, monkeypatch, auth_mode, request):
    request.getfixturevalue("auth_on" if auth_mode == "on" else "auth_off")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "s3cret")
    headers = [
        (b"content-type", b"application/json"),
        (b"accept", b"application/json, text/event-stream"),
        (b"host", _LOCAL_HOST.encode()),
        (b"authorization", "Bearer s3cré".encode("latin-1")),
    ]
    with app_with_lifespan as client:
        resp = client.post("/mcp/", content=json.dumps(_initialize_payload()), headers=headers)
    assert resp.status_code == 401


def test_secret_matches_non_ascii_is_false():
    import auth

    assert auth._secret_matches("é", "s3cret") is False
    assert auth._secret_matches("s3cret", "s3cret") is True


def test_mcp_foreign_host_unauthenticated_is_401(app_with_lifespan, auth_on):
    with app_with_lifespan as client:
        resp = _post(client, _initialize_payload(), host="lumogis-graph:8001")
    assert resp.status_code == 401
    assert resp.json() == {"error": "missing token"}


def test_mcp_foreign_host_with_valid_jwt_is_421(app_with_lifespan, auth_on, recorder):
    """Pins the SDK's DNS-rebinding guard the reachability record cites.

    If this fails after an SDK bump, update the record in auth.py / README, not the test.
    """
    with app_with_lifespan as client:
        resp = _post(client, _initialize_payload(), _bearer(_token()), host="lumogis-graph:8001")
    assert resp.status_code == 421
    assert recorder == []


def test_mcp_second_request_without_bearer_is_401(app_with_lifespan, auth_on, recorder):
    with app_with_lifespan as client:
        first = _call(client, "graph.query_ego", {"entity": "Ada"}, _bearer(_token()))
        second = _call(client, "graph.query_ego", {"entity": "Ada"})
    assert first.status_code == 200
    assert second.status_code == 401
    assert recorder == ["alice"]


def test_mcp_caller_reset_when_call_next_raises(monkeypatch):
    """Discriminating reset test: fails if the ``try/finally`` reset is removed.

    Runs the middleware and the check in one coroutine, because TestClient runs the
    app on another thread (a leak would not show in the test thread's ContextVar).
    """
    import auth
    from starlette.requests import Request

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp/",
        "headers": [],
        "query_string": b"",
        "server": ("testserver", 80),
        "scheme": "http",
    }

    async def _call_next(_request):
        assert auth.current_mcp_user().user_id == "default"
        raise RuntimeError("boom")

    async def _run():
        with pytest.raises(RuntimeError):
            await auth.auth_middleware(Request(scope), _call_next)
        return auth._MCP_CALLER.get()

    assert asyncio.run(_run()) is None


def test_mcp_path_match_is_segment_safe():
    import auth

    assert auth._is_mcp_path("/mcp") is True
    assert auth._is_mcp_path("/mcp/") is True
    assert auth._is_mcp_path("/mcpx") is False


# ---------------------------------------------------------------------------
# No user_id on any tool
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "args"),
    [*_READ_TOOLS, ("graph.health", {})],
)
def test_mcp_stray_user_id_refused(app_with_lifespan, auth_on, recorder, tool, args):
    with app_with_lifespan as client:
        resp = _call(client, tool, {**args, "user_id": "bob"}, _bearer(_token()))
    assert resp.status_code == 200, resp.text
    assert _is_error(resp)
    assert "remove the user_id argument" in _text(resp)
    assert recorder == []


def test_mcp_tools_advertise_no_user_id():
    import asyncio as _asyncio

    from kg_mcp.server import build_fastmcp

    tools = _asyncio.run(build_fastmcp().list_tools())
    assert len(tools) == 6
    for tool in tools:
        assert "user_id" not in tool.inputSchema.get("properties", {}), tool.name


def test_fastmcp_call_tool_signature_pinned():
    from mcp.server.fastmcp import FastMCP

    assert list(inspect.signature(FastMCP.call_tool).parameters) == ["self", "name", "arguments"]


# ---------------------------------------------------------------------------
# graph.backfill
# ---------------------------------------------------------------------------


def test_mcp_backfill_refused_for_non_admin(app_with_lifespan, auth_on, reconcile):
    with app_with_lifespan as client:
        resp = _call(client, "graph.backfill", {}, _bearer(_token(role="user")))
    assert resp.status_code == 200
    assert _is_error(resp)
    assert reconcile == []


def test_mcp_backfill_runs_for_admin_off_the_event_loop(app_with_lifespan, auth_on, reconcile):
    from graph import routes

    with app_with_lifespan as client:
        resp = _call(client, "graph.backfill", {}, _bearer(_token(sub="admin-1", role="admin")))
    assert resp.status_code == 200 and not _is_error(resp), resp.text
    assert len(reconcile) == 1
    assert reconcile[0]["on_loop"] is False
    assert not routes._backfill_running.is_set()


def test_mcp_backfill_clears_flag_when_reconciliation_raises(
    app_with_lifespan, auth_on, monkeypatch
):
    import graph.reconcile as gr
    from graph import routes

    def _boom(limit_per_type=None):
        raise RuntimeError("reconcile failed")

    monkeypatch.setattr(gr, "run_reconciliation", _boom)
    with app_with_lifespan as client:
        resp = _call(client, "graph.backfill", {}, _bearer(_token(sub="admin-1", role="admin")))
    assert _is_error(resp)
    assert not routes._backfill_running.is_set()


def test_mcp_backfill_refused_while_running(app_with_lifespan, auth_on, reconcile):
    from graph import routes

    routes._backfill_running.set()
    with app_with_lifespan as client:
        resp = _call(client, "graph.backfill", {}, _bearer(_token(sub="admin-1", role="admin")))
    assert _is_error(resp)
    assert "already running" in _text(resp)
    assert reconcile == []


def test_mcp_backfill_refused_when_graph_admin_token_set(
    app_with_lifespan, auth_off, monkeypatch, reconcile
):
    monkeypatch.setenv("GRAPH_ADMIN_TOKEN", "x")
    with app_with_lifespan as client:
        resp = _call(client, "graph.backfill", {})
    assert _is_error(resp)
    assert "POST /graph/backfill" in _text(resp)
    assert reconcile == []


def test_mcp_backfill_allowed_as_default_admin_when_auth_off(
    app_with_lifespan, auth_off, reconcile
):
    with app_with_lifespan as client:
        resp = _call(client, "graph.backfill", {})
    assert resp.status_code == 200 and not _is_error(resp), resp.text
    assert len(reconcile) == 1
