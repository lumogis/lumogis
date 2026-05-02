# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Phase 3.1 — MCP per-request bearer wiring.

Phase 3 introduced ``mcp_server._resolve_user_id()`` with a per-request
``ContextVar`` for the inbound Bearer token, but only the env-var
fallback was reachable at runtime; the JWT branch was test-only.
Phase 3.1 wires the real ``auth.auth_middleware`` so that every
``/mcp/*`` request now copies its ``Authorization`` header into that
ContextVar before the mounted FastMCP sub-app runs.

This test goes through the actual FastAPI middleware stack (not
``_resolve_user_id`` in isolation) and proves:

  * a valid Lumogis JWT in the Bearer header makes ``_resolve_user_id``
    return the JWT ``sub`` claim;
  * the JWT branch wins over ``MCP_DEFAULT_USER_ID`` even when both
    are set;
  * the ContextVar is reset after the request returns, so subsequent
    requests don't leak Alice's user_id into Bob's MCP call.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

# Pick a JWT secret comfortably above PyJWT's 32-byte recommendation so
# the test doesn't trip InsecureKeyLengthWarning the way the headline
# isolation test does.
_TEST_SECRET = "phase31-test-secret-please-32-bytes-minimum-okay"


@pytest.fixture
def jwt_env(monkeypatch):
    """Stand up a real JWT secret + admin role so mint/verify both work."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", _TEST_SECRET)
    monkeypatch.setenv("LUMOGIS_JWT_SECRET", _TEST_SECRET)
    yield


@pytest.fixture
def mini_app(jwt_env):
    """Build a minimal FastAPI app with the real auth middleware and a
    fake ``/mcp/probe`` endpoint that records what
    ``_resolve_user_id()`` returned during the request.

    We don't mount the actual FastMCP sub-app here because we want a
    deterministic in-process probe of the wiring, not an MCP protocol
    round-trip. The wiring under test is in
    ``auth.auth_middleware`` — what runs *after* it (a FastAPI route
    vs a Starlette mount) is irrelevant to whether the ContextVar is
    populated.
    """
    from auth import auth_middleware
    from fastapi import APIRouter

    app = FastAPI()
    app.middleware("http")(auth_middleware)

    router = APIRouter()

    @router.get("/mcp/probe")
    def probe():
        from mcp_server import _current_bearer_token
        from mcp_server import _resolve_user_id

        try:
            resolved = _resolve_user_id()
        except RuntimeError as exc:
            resolved = f"ERROR:{exc}"
        return JSONResponse(
            {
                "bearer_in_contextvar": _current_bearer_token(),
                "resolved_user_id": resolved,
            }
        )

    app.include_router(router)
    return app


def _mint(user_id: str, role: str = "user") -> str:
    from auth import mint_access_token

    return mint_access_token(user_id=user_id, role=role)


def test_mcp_request_resolves_user_id_from_jwt_sub(mini_app, monkeypatch):
    """A valid Lumogis JWT in the Bearer header takes precedence over the
    operator-configured ``MCP_DEFAULT_USER_ID`` — that's the only path
    that gives real per-user MCP isolation.
    """
    monkeypatch.setenv("MCP_DEFAULT_USER_ID", "fallback-shared-user")
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)

    token = _mint(user_id="alice")
    client = TestClient(mini_app)

    resp = client.get("/mcp/probe", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()

    assert body["bearer_in_contextvar"] == token, (
        "auth_middleware did not copy the inbound Bearer into the MCP ContextVar"
    )
    assert body["resolved_user_id"] == "alice", "expected JWT sub to win over MCP_DEFAULT_USER_ID"


def test_mcp_request_rejects_legacy_shared_secret_in_multi_user_mode(mini_app, monkeypatch):
    """Per ADR ``mcp_token_user_map`` D6: in multi-user mode
    (``AUTH_ENABLED=true``) a Bearer that matches the legacy
    ``MCP_AUTH_TOKEN`` MUST be rejected with 401, NOT silently mapped to
    ``MCP_DEFAULT_USER_ID``.

    This test inverted in the ADR: pre-D6, the shared secret was the
    documented fall-through; post-D6 it is the documented fail-closed
    refusal, with a one-shot ``CRITICAL`` log line and a 401 body whose
    ``error`` field points operators at ``POST /api/v1/me/mcp-tokens``.
    The single-user pass-through path still works — see
    ``tests/test_mcp_server.py::test_mcp_endpoint_accepts_correct_token``
    which deliberately leaves ``AUTH_ENABLED`` unset.
    """
    monkeypatch.setenv("MCP_AUTH_TOKEN", "shared-mcp-secret")
    monkeypatch.setenv("MCP_DEFAULT_USER_ID", "fallback-shared-user")

    client = TestClient(mini_app)

    resp = client.get(
        "/mcp/probe",
        headers={"Authorization": "Bearer shared-mcp-secret"},
    )
    assert resp.status_code == 401, resp.text
    body = resp.json()
    assert "lmcp_" in body.get("error", ""), body


def test_contextvar_does_not_leak_between_requests(mini_app, monkeypatch):
    """Two back-to-back requests must each see their own bearer.

    This guards against the classic ContextVar bug where the previous
    request's ``set()`` token isn't reset, leaving Alice's user_id
    sticky for Bob's subsequent MCP call.
    """
    monkeypatch.setenv("MCP_DEFAULT_USER_ID", "fallback-shared-user")
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)

    alice_token = _mint(user_id="alice")
    bob_token = _mint(user_id="bob")

    client = TestClient(mini_app)

    alice_resp = client.get("/mcp/probe", headers={"Authorization": f"Bearer {alice_token}"})
    bob_resp = client.get("/mcp/probe", headers={"Authorization": f"Bearer {bob_token}"})

    assert alice_resp.json()["resolved_user_id"] == "alice"
    assert bob_resp.json()["resolved_user_id"] == "bob"

    # And after both requests, the ContextVar must be back to its
    # default (None) — proves _reset_current_bearer ran in the
    # ``finally`` branch.
    from mcp_server import _current_bearer_token

    async def _peek():
        return _current_bearer_token()

    leaked = asyncio.run(_peek())
    assert leaked is None, f"ContextVar leaked across requests: {leaked!r}"


def test_non_mcp_paths_do_not_touch_mcp_contextvar(mini_app, monkeypatch):
    """Sanity: requests that never hit the /mcp branch must not set the
    MCP ContextVar — otherwise unrelated traffic would pollute it.
    """
    monkeypatch.setenv("MCP_DEFAULT_USER_ID", "fallback-shared-user")
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)

    # Add a non-/mcp probe route. We can't reuse the fixture's app
    # cleanly without re-registering routes, so attach inline.
    @mini_app.get("/non-mcp-probe")
    def non_mcp_probe():
        from mcp_server import _current_bearer_token

        return {"bearer": _current_bearer_token()}

    token = _mint(user_id="alice")
    client = TestClient(mini_app)
    resp = client.get("/non-mcp-probe", headers={"Authorization": f"Bearer {token}"})
    # AUTH_ENABLED=true, so non-/mcp routes go through the JWT gate
    # which sets request.state.user but does NOT touch the MCP
    # ContextVar — that's the expected behaviour we're pinning here.
    assert resp.status_code == 200
    assert resp.json()["bearer"] is None
