# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis

"""Auth session / token-version regressions (LUM-29)."""

from __future__ import annotations

import os
import time

import jwt
from fastapi.testclient import TestClient
from tests.test_auth_phase2 import _client

pytest_plugins = ("tests.test_auth_phase1",)


def test_require_tv_claim_rejects_legacy_jwt(users_store, auth_env, monkeypatch):
    monkeypatch.setenv("LUMOGIS_REQUIRE_TV_CLAIM", "true")
    import main
    import services.users as users_svc

    users_svc.create_user("alice-tv@home.lan", "verylongpassword12", "admin")
    u = users_svc.get_user_by_email("alice-tv@home.lan")
    assert u is not None
    legacy = jwt.encode(
        {
            "sub": u.id,
            "role": "admin",
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        },
        os.environ["AUTH_SECRET"],
        algorithm="HS256",
    )
    with TestClient(main.app) as client:
        resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {legacy}"})
        assert resp.status_code == 401


def test_password_change_invalidates_prior_access_token_with_tv_claim(
    users_store,
    auth_env,
    monkeypatch,
):
    monkeypatch.setenv("LUMOGIS_TOKEN_VERSION_CACHE_TTL_SECONDS", "0")
    import auth as auth_mod
    import services.users as users_svc

    with _client(users_store) as client:
        admin = users_svc.get_user_by_email("admin@home.lan")
        assert admin is not None
        token = auth_mod.mint_access_token(
            admin.id,
            "admin",
            session_id="lumogis-test-access-session",
            token_version=int(admin.token_version),
        )
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 200

        r = client.post(
            "/api/v1/me/password",
            headers=headers,
            json={
                "current_password": "verylongpassword12",
                "new_password": "brandnewpassword12tv",
            },
        )
        assert r.status_code == 200

        stale = client.get("/api/v1/auth/me", headers=headers)
        assert stale.status_code == 401
