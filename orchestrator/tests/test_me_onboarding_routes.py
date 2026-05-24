# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route tests for ``GET``/``PATCH /api/v1/me/onboarding`` (LUM-165)."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from tests.test_auth_phase1 import FakeUsersStore


class _OnboardingFakeStore(FakeUsersStore):
    """``FakeUsersStore`` + ``onboarding_completed_at`` column semantics."""

    def execute(self, query: str, params: tuple | None = None) -> None:
        q = " ".join(query.split()).lower()
        p = params or ()
        if q.startswith("insert into users"):
            super().execute(query, params)
            uid = str(p[0])
            row = self.rows.get(uid)
            if row is not None:
                row.setdefault("onboarding_completed_at", None)
            return
        super().execute(query, params)

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = " ".join(query.split()).lower()
        p = params or ()
        if "select onboarding_completed_at from users where id =" in q:
            uid = str(p[0])
            row = self.rows.get(uid)
            if row is None:
                return None
            return {"onboarding_completed_at": row.get("onboarding_completed_at")}
        if "update users set onboarding_completed_at" in q and "returning onboarding_completed_at" in q:
            uid = str(p[0])
            row = self.rows.get(uid)
            if row is None:
                return None
            if row.get("onboarding_completed_at") is None:
                row["onboarding_completed_at"] = datetime.now(timezone.utc)
            return {"onboarding_completed_at": row["onboarding_completed_at"]}
        return super().fetch_one(query, params)


@pytest.fixture
def store(monkeypatch):
    import config as _config

    s = _OnboardingFakeStore()
    _config._instances["metadata_store"] = s
    yield s
    _config._instances.pop("metadata_store", None)


@pytest.fixture
def dev_env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    yield


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-onboarding-access-secret")
    monkeypatch.setenv(
        "LUMOGIS_JWT_REFRESH_SECRET",
        "test-onboarding-refresh-secret",
    )
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("REFRESH_TOKEN_TTL_SECONDS", "3600")
    monkeypatch.setenv("LUMOGIS_REFRESH_COOKIE_SECURE", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    yield
    from routes.auth import _reset_rate_limit_for_tests

    _reset_rate_limit_for_tests()


def _mint_jwt(user_id: str, role: str) -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "role": role,
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        },
        os.environ["AUTH_SECRET"],
        algorithm="HS256",
    )


@contextmanager
def _client():
    import main

    with TestClient(main.app) as client:
        yield client


def _seed(store: _OnboardingFakeStore, *, email: str, role: str) -> str:
    import services.users as users_svc

    if users_svc.get_user_by_email(email) is None:
        users_svc.create_user(email, "verylongpassword12", role)
    user = users_svc.get_user_by_email(email)
    assert user is not None
    return user.id


def test_onboarding_get_dev_synthetic(store, dev_env):
    with _client() as client:
        r = client.get("/api/v1/me/onboarding")
    assert r.status_code == 200, r.text
    assert r.headers.get("cache-control") == "no-store"
    body = r.json()
    assert body["completed_at"] is not None


def test_onboarding_patch_dev_noop(store, dev_env):
    with _client() as client:
        r = client.patch("/api/v1/me/onboarding", json={"completed": True})
    assert r.status_code == 200, r.text
    assert r.headers.get("cache-control") == "no-store"
    assert r.json()["completed_at"] is not None


def test_onboarding_get_auth_null_then_patch(store, auth_env):
    uid = _seed(store, email="onb-a@home.lan", role="user")
    tok = _mint_jwt(uid, "user")
    hdr = {"Authorization": f"Bearer {tok}"}
    with _client() as client:
        g = client.get("/api/v1/me/onboarding", headers=hdr)
        assert g.status_code == 200, g.text
        assert g.json()["completed_at"] is None
        p = client.patch("/api/v1/me/onboarding", headers=hdr, json={"completed": True})
        assert p.status_code == 200, p.text
        iso1 = p.json()["completed_at"]
        p2 = client.patch("/api/v1/me/onboarding", headers=hdr, json={"completed": True})
        assert p2.status_code == 200, p2.text
        iso2 = p2.json()["completed_at"]
        assert iso1 == iso2
        g2 = client.get("/api/v1/me/onboarding", headers=hdr)
        assert g2.json()["completed_at"] == iso1


def test_onboarding_patch_422_bad_body(store, auth_env):
    uid = _seed(store, email="onb-b@home.lan", role="user")
    tok = _mint_jwt(uid, "user")
    hdr = {"Authorization": f"Bearer {tok}"}
    with _client() as client:
        r = client.patch("/api/v1/me/onboarding", headers=hdr, json={"completed": False})
    assert r.status_code == 422


def test_onboarding_get_404_missing_user_row(store, auth_env):
    uid = _seed(store, email="onb-c@home.lan", role="user")
    tok = _mint_jwt(uid, "user")
    store.rows.pop(uid, None)
    hdr = {"Authorization": f"Bearer {tok}"}
    with _client() as client:
        r = client.get("/api/v1/me/onboarding", headers=hdr)
    assert r.status_code == 404


def test_onboarding_get_401_without_token(store, auth_env):
    with _client() as client:
        r = client.get("/api/v1/me/onboarding")
    assert r.status_code == 401
