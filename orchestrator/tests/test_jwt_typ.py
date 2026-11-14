# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Access and refresh JWTs carry ``typ`` and are never interchangeable (LUM-885).

Security audit #2 AUTH-002: with a refresh secret equal to ``AUTH_SECRET`` (the
removed fallback, or an operator-set shared value) a refresh JWT verified as an
access token on every verifier that did not check ``typ``. RFC 8725 §3.11/3.12.
"""

from __future__ import annotations

import time

import auth
import jwt
import pytest
import services.users as users_svc
from fastapi import APIRouter
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

pytest_plugins = ("tests.test_auth_phase1",)

ACCESS_SECRET = "test-access-secret-do-not-use-in-prod"
REFRESH_SECRET = "test-refresh-secret-do-not-use-in-prod"


def _claims(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False})


def _payload(**extra) -> dict:
    now = int(time.time())
    return {"sub": "alice", "iat": now, "exp": now + 300, **extra}


# ---------------------------------------------------------------------------
# Mint
# ---------------------------------------------------------------------------


def test_mint_access_has_typ_access(auth_env):
    assert _claims(auth.mint_access_token("alice", "user"))["typ"] == "access"


def test_mint_refresh_has_typ_refresh(auth_env):
    assert _claims(auth.mint_refresh_token("alice", "jti-1"))["typ"] == "refresh"


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


def test_verify_token_requires_typ_access(auth_env):
    typeless = jwt.encode(_payload(role="user"), ACCESS_SECRET, algorithm="HS256")
    refresh_typed = jwt.encode(_payload(typ="refresh"), ACCESS_SECRET, algorithm="HS256")
    access = auth.mint_access_token("alice", "user")

    assert auth.verify_token(typeless) is None
    assert auth.verify_token(refresh_typed) is None
    got = auth.verify_token(access)
    assert got is not None and got["sub"] == "alice"


def test_verify_refresh_token_typ_rules(auth_env):
    typeless = jwt.encode(_payload(jti="j"), REFRESH_SECRET, algorithm="HS256")
    refresh = jwt.encode(_payload(jti="j", typ="refresh"), REFRESH_SECRET, algorithm="HS256")
    access_typed = jwt.encode(_payload(jti="j", typ="access"), REFRESH_SECRET, algorithm="HS256")

    # Legacy tolerance: refresh JWTs minted before LUM-885 carry no ``typ``.
    assert auth.verify_refresh_token(typeless) is not None
    assert auth.verify_refresh_token(refresh) is not None
    assert auth.verify_refresh_token(access_typed) is None


def test_refresh_secret_has_no_auth_secret_fallback(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", ACCESS_SECRET)
    monkeypatch.delenv("LUMOGIS_JWT_REFRESH_SECRET", raising=False)
    monkeypatch.delenv("JWT_REFRESH_SECRET", raising=False)

    assert auth._refresh_secret() == ""
    with pytest.raises(RuntimeError):
        auth.mint_refresh_token("alice", "jti-1")
    assert auth.verify_refresh_token("anything") is None


def test_refresh_secret_resolution_order(monkeypatch):
    monkeypatch.setenv("JWT_REFRESH_SECRET", "legacy-name")
    monkeypatch.setenv("LUMOGIS_JWT_REFRESH_SECRET", "preferred-name")
    assert auth._refresh_secret() == "preferred-name"
    monkeypatch.delenv("LUMOGIS_JWT_REFRESH_SECRET")
    assert auth._refresh_secret() == "legacy-name"


# ---------------------------------------------------------------------------
# End to end through the middleware
# ---------------------------------------------------------------------------


def test_refresh_jwt_as_bearer_rejected_even_with_shared_secret(users_store, auth_env, monkeypatch):
    """AUTH-002 exactly: refresh secret == AUTH_SECRET, refresh JWT as a Bearer."""
    import main

    monkeypatch.setenv("LUMOGIS_JWT_REFRESH_SECRET", ACCESS_SECRET)
    user = users_svc.create_user("alice@home.lan", "verylongpassword12", "admin")

    with TestClient(main.app) as client:
        # Negative control: a freshly minted access JWT for the same user works,
        # so the 401s below cannot come from a malformed token.
        access = auth.mint_access_token(user.id, "admin")
        ok = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"})
        assert ok.status_code == 200

        refresh = auth.mint_refresh_token(user.id, "jti-1")
        r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {refresh}"})
        assert r.status_code == 401

        # The exact attack: a legacy typ-less refresh payload signed with the
        # shared secret, claiming admin.
        legacy = jwt.encode(
            _payload(sub=user.id, jti="jti-1", role="admin"), ACCESS_SECRET, algorithm="HS256"
        )
        r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {legacy}"})
        assert r.status_code == 401


@pytest.fixture
def mcp_probe_app():
    app = FastAPI()
    app.middleware("http")(auth.auth_middleware)
    router = APIRouter()

    @router.get("/mcp/probe")
    def probe():
        return JSONResponse({"ok": True})

    app.include_router(router)
    return app


def test_refresh_jwt_as_mcp_bearer_rejected(auth_env, mcp_probe_app, monkeypatch):
    monkeypatch.setenv("LUMOGIS_JWT_REFRESH_SECRET", ACCESS_SECRET)  # shared: worst case
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    client = TestClient(mcp_probe_app)

    # Negative control: a real access JWT passes the /mcp bearer gate.
    ok = client.get(
        "/mcp/probe", headers={"Authorization": f"Bearer {auth.mint_access_token('alice', 'user')}"}
    )
    assert ok.status_code == 200

    refresh = auth.mint_refresh_token("alice", "jti-1")
    assert (
        client.get("/mcp/probe", headers={"Authorization": f"Bearer {refresh}"}).status_code == 401
    )
    typeless = jwt.encode(_payload(role="admin"), ACCESS_SECRET, algorithm="HS256")
    assert (
        client.get("/mcp/probe", headers={"Authorization": f"Bearer {typeless}"}).status_code == 401
    )


def test_typeless_access_token_not_rescued_by_mcp_auth_token(auth_env, mcp_probe_app, monkeypatch):
    """A typ-less JWT falls through step 4 and must not match the legacy shared token."""
    monkeypatch.setenv("MCP_AUTH_TOKEN", "a-different-shared-secret")
    typeless = jwt.encode(_payload(role="admin"), ACCESS_SECRET, algorithm="HS256")
    r = TestClient(mcp_probe_app).get("/mcp/probe", headers={"Authorization": f"Bearer {typeless}"})
    assert r.status_code == 401
