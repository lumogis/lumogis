# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route tests for ``GET``/``PATCH /api/v1/me/wow-state`` (LUM-216)."""

from __future__ import annotations

import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from tests.test_me_onboarding_routes import _OnboardingFakeStore


class _WowFakeStore(_OnboardingFakeStore):
    """Onboarding fake store + in-memory ``entities`` and ``wow_dismissed_at``."""

    def __init__(self) -> None:
        super().__init__()
        self.entities: list[dict] = []

    def execute(self, query: str, params: tuple | None = None) -> None:
        q = " ".join(query.split()).lower()
        p = params or ()
        if q.startswith("insert into users"):
            super().execute(query, params)
            uid = str(p[0])
            row = self.rows.get(uid)
            if row is not None:
                row.setdefault("wow_dismissed_at", None)
            return
        super().execute(query, params)

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = " ".join(query.split()).lower()
        p = params or ()
        if "select wow_dismissed_at from users where id =" in q:
            uid = str(p[0])
            row = self.rows.get(uid)
            if row is None:
                return None
            return {"wow_dismissed_at": row.get("wow_dismissed_at")}
        if "update users set wow_dismissed_at" in q and "returning wow_dismissed_at" in q:
            uid = str(p[0])
            row = self.rows.get(uid)
            if row is None:
                return None
            if row.get("wow_dismissed_at") is None:
                row["wow_dismissed_at"] = datetime.now(timezone.utc)
            return {"wow_dismissed_at": row["wow_dismissed_at"]}
        if "select count(*)::int as n from entities" in q:
            uid = self._user_id_from_entity_where(q, p)
            n = len(self._filter_entities(uid, q))
            return {"n": n}
        return super().fetch_one(query, params)

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        q = " ".join(query.split()).lower()
        p = params or ()
        if "from entities" in q and "order by mention_count desc" in q:
            uid = self._user_id_from_entity_where(q, p)
            limit = int(p[-1])
            rows = sorted(
                self._filter_entities(uid, q),
                key=lambda r: int(r.get("mention_count") or 0),
                reverse=True,
            )[:limit]
            return [
                {
                    "entity_id": r["entity_id"],
                    "name": r["name"],
                    "entity_type": r["entity_type"],
                    "mention_count": r["mention_count"],
                    "scope": r.get("scope", "personal"),
                }
                for r in rows
            ]
        return super().fetch_all(query, params)

    def _user_id_from_entity_where(self, q: str, p: tuple) -> str:
        if "user_id = %s" in q or "user_id =%s" in q.replace(" ", ""):
            return str(p[0])
        return str(p[0])

    def _filter_entities(self, viewer_id: str, q: str) -> list[dict]:
        out: list[dict] = []
        for row in self.entities:
            if row.get("is_staged") is True:
                continue
            scope = row.get("scope", "personal")
            if scope == "personal":
                if row.get("user_id") != viewer_id:
                    continue
            elif scope not in ("shared", "system"):
                continue
            out.append(row)
        return out

    def insert_entity(
        self,
        *,
        user_id: str,
        name: str,
        mention_count: int = 1,
        is_staged: bool = False,
        scope: str = "personal",
    ) -> str:
        eid = str(uuid.uuid4())
        self.entities.append(
            {
                "entity_id": eid,
                "user_id": user_id,
                "name": name,
                "entity_type": "Person",
                "mention_count": mention_count,
                "scope": scope,
                "is_staged": is_staged,
            }
        )
        return eid


@pytest.fixture
def store(monkeypatch):
    import config as _config

    s = _WowFakeStore()
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
    monkeypatch.setenv("AUTH_SECRET", "test-wow-access-secret")
    monkeypatch.setenv(
        "LUMOGIS_JWT_REFRESH_SECRET",
        "test-wow-refresh-secret",
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


def _seed(store: _WowFakeStore, *, email: str, role: str) -> str:
    import services.users as users_svc

    if users_svc.get_user_by_email(email) is None:
        users_svc.create_user(email, "verylongpassword12", role)
    user = users_svc.get_user_by_email(email)
    assert user is not None
    return user.id


def test_wow_get_dev_synthetic(store, dev_env):
    with _client() as client:
        r = client.get("/api/v1/me/wow-state")
    assert r.status_code == 200, r.text
    assert r.headers.get("cache-control") == "no-store"
    body = r.json()
    assert body["entities_ready"] is True
    assert body["top_entities"] == []
    assert body["wow_dismissed_at"] is None
    assert body["onboarding_completed_at"] is not None


def test_wow_get_auth_null_not_ready(store, auth_env):
    uid = _seed(store, email="wow-a@home.lan", role="user")
    tok = _mint_jwt(uid, "user")
    hdr = {"Authorization": f"Bearer {tok}"}
    with _client() as client:
        r = client.get("/api/v1/me/wow-state", headers=hdr)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entities_ready"] is False
    assert body["top_entities"] == []


def test_wow_get_auth_with_entities(store, auth_env):
    uid = _seed(store, email="wow-b@home.lan", role="user")
    store.insert_entity(user_id=uid, name="Alpha", mention_count=3)
    store.insert_entity(user_id=uid, name="Beta", mention_count=2)
    store.insert_entity(user_id=uid, name="Gamma", mention_count=1)
    tok = _mint_jwt(uid, "user")
    hdr = {"Authorization": f"Bearer {tok}"}
    with _client() as client:
        r = client.get("/api/v1/me/wow-state", headers=hdr)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entities_ready"] is True
    assert len(body["top_entities"]) <= 5
    assert body["top_entities"][0]["name"] == "Alpha"
    assert body["top_entities"][0]["mention_count"] == 3


def test_wow_staged_entity_excluded(store, auth_env):
    uid = _seed(store, email="wow-c@home.lan", role="user")
    store.insert_entity(user_id=uid, name="Staged Only", is_staged=True)
    tok = _mint_jwt(uid, "user")
    hdr = {"Authorization": f"Bearer {tok}"}
    with _client() as client:
        r = client.get("/api/v1/me/wow-state", headers=hdr)
    body = r.json()
    assert body["entities_ready"] is False
    assert body["top_entities"] == []


def test_wow_staged_mixed(store, auth_env):
    uid = _seed(store, email="wow-d@home.lan", role="user")
    store.insert_entity(user_id=uid, name="Staged", is_staged=True)
    store.insert_entity(user_id=uid, name="Visible", mention_count=5)
    tok = _mint_jwt(uid, "user")
    hdr = {"Authorization": f"Bearer {tok}"}
    with _client() as client:
        r = client.get("/api/v1/me/wow-state", headers=hdr)
    body = r.json()
    assert body["entities_ready"] is True
    names = {e["name"] for e in body["top_entities"]}
    assert "Staged" not in names
    assert "Visible" in names


def test_wow_patch_idempotent(store, auth_env):
    uid = _seed(store, email="wow-e@home.lan", role="user")
    tok = _mint_jwt(uid, "user")
    hdr = {"Authorization": f"Bearer {tok}"}
    with _client() as client:
        p1 = client.patch("/api/v1/me/wow-state", headers=hdr, json={"dismissed": True})
        assert p1.status_code == 200, p1.text
        iso1 = p1.json()["wow_dismissed_at"]
        p2 = client.patch("/api/v1/me/wow-state", headers=hdr, json={"dismissed": True})
        assert p2.status_code == 200, p2.text
        iso2 = p2.json()["wow_dismissed_at"]
        assert iso1 == iso2


def test_wow_patch_422_bad_body(store, auth_env):
    uid = _seed(store, email="wow-f@home.lan", role="user")
    tok = _mint_jwt(uid, "user")
    hdr = {"Authorization": f"Bearer {tok}"}
    with _client() as client:
        r = client.patch("/api/v1/me/wow-state", headers=hdr, json={"dismissed": False})
    assert r.status_code == 422


def test_wow_get_401_without_token(store, auth_env):
    with _client() as client:
        r = client.get("/api/v1/me/wow-state")
    assert r.status_code == 401


def test_wow_get_404_missing_user_row(store, auth_env):
    uid = _seed(store, email="wow-g@home.lan", role="user")
    tok = _mint_jwt(uid, "user")
    store.rows.pop(uid, None)
    hdr = {"Authorization": f"Bearer {tok}"}
    with _client() as client:
        r = client.get("/api/v1/me/wow-state", headers=hdr)
    assert r.status_code == 404
