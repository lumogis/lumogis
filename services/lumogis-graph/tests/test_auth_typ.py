# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The KG verifier accepts Core *access* JWTs only (LUM-885).

Security audit #2 AUTH-002: the graph service verifies Core access tokens under
the same ``AUTH_SECRET``. A refresh JWT signed with a shared secret, or a token
with no ``typ``, must not authenticate here either.

PyJWT is a KG runtime dependency (``requirements.txt``, LUM-915). It is imported
at module level on purpose: a lane without it must fail, not skip. Tokens carry
``tv=0`` and the autouse fixture seeds a matching ``users`` row (the ``tv`` check).
"""

from __future__ import annotations

import time

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

SECRET = "kg-test-access-secret-0123456789abcdef"


@pytest.fixture(autouse=True)
def _auth_on(monkeypatch, mock_metadata_store):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", SECRET)
    mock_metadata_store.token_versions = {"admin-1": 0}


def _token(tv: int | None = 0, **extra) -> str:
    now = int(time.time())
    payload = {"sub": "admin-1", "role": "admin", "iat": now, "exp": now + 300, **extra}
    if tv is not None:
        payload["tv"] = tv
    return jwt.encode(payload, SECRET, algorithm="HS256")


def test_kg_verify_rejects_typeless_and_refresh():
    import auth

    assert auth._verify_token(_token()) is None
    assert auth._verify_token(_token(typ="refresh")) is None
    assert auth._verify_token(_token(typ="something-else")) is None


def test_kg_verify_accepts_access():
    import auth

    payload = auth._verify_token(_token(typ="access"))
    assert payload is not None
    assert payload["sub"] == "admin-1"


def test_kg_verify_still_rejects_bad_signature_and_expired():
    import auth

    now = int(time.time())
    forged = jwt.encode(
        {"sub": "x", "typ": "access", "iat": now, "exp": now + 60}, "wrong", algorithm="HS256"
    )
    expired = jwt.encode(
        {"sub": "x", "typ": "access", "iat": now - 600, "exp": now - 300},
        SECRET,
        algorithm="HS256",
    )
    assert auth._verify_token(forged) is None
    assert auth._verify_token(expired) is None


def _mgm_client() -> TestClient:
    from auth import auth_middleware
    from routes.mgm import router

    app = FastAPI()
    app.middleware("http")(auth_middleware)
    app.include_router(router)
    return TestClient(app)


def test_kg_mgm_rejects_refresh_typed_bearer():
    client = _mgm_client()

    # Negative control: the same claims typed as access reach /mgm.
    ok = client.get("/mgm", headers={"Authorization": f"Bearer {_token(typ='access')}"})
    assert ok.status_code == 200

    for bad in (_token(typ="refresh"), _token()):
        r = client.get("/mgm", headers={"Authorization": f"Bearer {bad}"})
        assert r.status_code == 401


def test_graph_assets_require_admin():
    """/graph/assets/* shares the page's admin gate (LUM-941)."""
    client = _mgm_client()
    path = "/graph/assets/alpine-3.14.8.min.js"

    def _get(tok):
        headers = {"Authorization": f"Bearer {tok}"} if tok else {}
        return client.get(path, headers=headers)

    assert _get(None).status_code == 401
    assert _get(_token(typ="refresh")).status_code == 401
    assert _get(_token(typ="access", role="user")).status_code == 403
    ok = _get(_token(typ="access"))
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("text/javascript")


def test_requires_admin_covers_graph_assets():
    # Superseded in breadth by test_requires_admin_real_paths (LUM-915).
    import auth

    class _Url:
        def __init__(self, path):
            self.path = path

    class _Req:
        def __init__(self, path, method="GET"):
            self.url = _Url(path)
            self.method = method

    assert auth._requires_admin(_Req("/graph/assets/x")) is True
    assert auth._requires_admin(_Req("/graph/stats")) is False
    assert auth._requires_admin(_Req("/mgm")) is True
