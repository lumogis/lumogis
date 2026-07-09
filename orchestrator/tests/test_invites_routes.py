# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route tests for household invite endpoints (LUM-186)."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from tests.test_auth_phase1 import FakeUsersStore


class _InvitesFakeStore(FakeUsersStore):
    def __init__(self) -> None:
        super().__init__()
        self.invites: dict[str, dict] = {}

    @staticmethod
    def _norm(query: str) -> str:
        return " ".join(query.split()).lower()

    def execute(self, query: str, params: tuple | None = None) -> None:
        q = self._norm(query)
        p = params or ()
        if q.startswith("insert into user_invites"):
            iid, prefix, thash, role, allows, created_by, expires = p
            for row in self.invites.values():
                if row["used_at"] is None and row["revoked_at"] is None and row["token_prefix"] == prefix:
                    raise RuntimeError(
                        "duplicate key value violates unique constraint user_invites_active_prefix_uniq"
                    )
            self.invites[iid] = {
                "id": iid,
                "token_prefix": prefix,
                "token_hash": thash,
                "role": role,
                "allows_shared": allows,
                "created_by": created_by,
                "created_at": datetime.now(timezone.utc),
                "expires_at": expires,
                "used_at": None,
                "used_by": None,
                "revoked_at": None,
            }
            return
        if q.startswith("update user_invites set revoked_at"):
            (iid,) = p
            row = self.invites.get(iid)
            if row and row["used_at"] is None and row["revoked_at"] is None:
                row["revoked_at"] = datetime.now(timezone.utc)
            return
        return super().execute(query, params)

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = self._norm(query)
        p = params or ()
        if q.startswith("select * from user_invites where id ="):
            (iid,) = p
            row = self.invites.get(iid)
            return dict(row) if row else None
        if q.startswith("select id from user_invites where id ="):
            (iid,) = p
            row = self.invites.get(iid)
            if row and row["used_at"] is None and row["revoked_at"] is None:
                return {"id": iid}
            return None
        if q.startswith("select * from user_invites where token_prefix"):
            (prefix,) = p
            now = datetime.now(timezone.utc)
            for row in self.invites.values():
                if (
                    row["token_prefix"] == prefix
                    and row["used_at"] is None
                    and row["revoked_at"] is None
                    and row["expires_at"] > now
                ):
                    return dict(row)
            return None
        if q.startswith("update user_invites set used_at = now()") and "returning" in q:
            used_by, iid = p
            row = self.invites.get(iid)
            now = datetime.now(timezone.utc)
            if (
                row
                and row["used_at"] is None
                and row["revoked_at"] is None
                and row["expires_at"] > now
            ):
                row["used_at"] = now
                row["used_by"] = used_by
                return dict(row)
            return None
        return super().fetch_one(query, params)

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        q = self._norm(query)
        if q.startswith("select * from user_invites"):
            rows = sorted(self.invites.values(), key=lambda r: r["created_at"], reverse=True)
            return [dict(r) for r in rows]
        return super().fetch_all(query, params)


@pytest.fixture
def store(monkeypatch):
    import config as _config

    s = _InvitesFakeStore()
    _config._instances["metadata_store"] = s
    yield s
    _config._instances.pop("metadata_store", None)


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-invites-access-secret")
    monkeypatch.setenv("LUMOGIS_JWT_REFRESH_SECRET", "test-invites-refresh-secret")
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("REFRESH_TOKEN_TTL_SECONDS", "3600")
    monkeypatch.setenv("LUMOGIS_REFRESH_COOKIE_SECURE", "false")
    monkeypatch.setenv(
        "LUMOGIS_CREDENTIAL_KEY",
        "OlGLYckGIbBSt54y8XVmgb441LgKJWvvYoHnpQ_cv9A=",
    )
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    yield
    from routes.auth import _reset_rate_limit_for_tests
    from routes.invites import _reset_limiters_for_tests

    _reset_rate_limit_for_tests()
    _reset_limiters_for_tests()


@pytest.fixture
def dev_env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    yield


def _mint_jwt(user_id: str, role: str) -> str:
    return jwt.encode(
        {"sub": user_id, "role": role, "iat": int(time.time()), "exp": int(time.time()) + 600},
        os.environ["AUTH_SECRET"],
        algorithm="HS256",
    )


@contextmanager
def _client():
    import main

    with TestClient(main.app) as client:
        yield client


def _seed_admin(store: _InvitesFakeStore) -> tuple[str, str]:
    import services.users as users_svc

    if users_svc.get_user_by_email("admin@example.com") is None:
        users_svc.create_user("admin@example.com", "adminpassword12", "admin")
    user = users_svc.get_user_by_email("admin@example.com")
    assert user is not None
    return user.id, _mint_jwt(user.id, "admin")


def _mint_invite(admin_id: str) -> tuple[str, str]:
    from services import user_invites as invites_svc

    internal, plaintext = invites_svc.mint_invite(
        role="user",
        allows_shared=True,
        created_by=admin_id,
    )
    return plaintext, internal.id


def test_peek_200_without_bearer(auth_env, store):
    admin_id, admin_jwt = _seed_admin(store)
    token, _ = _mint_invite(admin_id)
    with _client() as client:
        r = client.get(f"/api/v1/invites/{token}")
    assert r.status_code == 200
    assert "allows_shared" in r.json()


def test_peek_404_bad_token(auth_env, store):
    _seed_admin(store)
    with _client() as client:
        r = client.get("/api/v1/invites/linv_notavalidtokenatallxxxxxxxxxxxxxx")
    assert r.status_code == 404


def test_redeem_200_creates_user_and_session(auth_env, store):
    admin_id, _ = _seed_admin(store)
    token, _ = _mint_invite(admin_id)
    with _client() as client:
        r = client.post(
            f"/api/v1/invites/{token}/redeem",
            json={"email": "member@example.com", "password": "securepass1234"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["email"] == "member@example.com"
    assert "access_token" in body
    assert body["invite_onboarding"]["allows_shared"] is True
    assert "lumogis_refresh" in r.cookies


def test_redeem_replay_404(auth_env, store):
    admin_id, _ = _seed_admin(store)
    token, _ = _mint_invite(admin_id)
    payload = {"email": "member@example.com", "password": "securepass1234"}
    with _client() as client:
        assert client.post(f"/api/v1/invites/{token}/redeem", json=payload).status_code == 200
        r2 = client.post(f"/api/v1/invites/{token}/redeem", json=payload)
    assert r2.status_code == 404


def test_redeem_duplicate_email_409(auth_env, store):
    import services.users as users_svc

    admin_id, _ = _seed_admin(store)
    users_svc.create_user("taken@example.com", "existingpass12", "user")
    token, invite_id = _mint_invite(admin_id)
    with _client() as client:
        r = client.post(
            f"/api/v1/invites/{token}/redeem",
            json={"email": "taken@example.com", "password": "securepass1234"},
        )
    assert r.status_code == 409
    assert store.invites[invite_id]["used_at"] is None


def test_non_admin_mint_403(auth_env, store):
    import services.users as users_svc

    _seed_admin(store)
    member = users_svc.create_user("member@example.com", "memberpass1234", "user")
    member_jwt = _mint_jwt(member.id, "user")
    with _client() as client:
        r = client.post(
            "/api/v1/admin/users/invites",
            headers={"Authorization": f"Bearer {member_jwt}"},
            json={"role": "user", "allows_shared": True},
        )
    assert r.status_code == 403


def test_auth_disabled_503(dev_env, store):
    with _client() as client:
        r = client.get("/api/v1/invites/linv_abc")
    assert r.status_code == 503


def test_peek_rate_limit_429(auth_env, store):
    admin_id, _ = _seed_admin(store)
    token, _ = _mint_invite(admin_id)
    with _client() as client:
        for _ in range(30):
            assert client.get(f"/api/v1/invites/{token}").status_code == 200
        r = client.get(f"/api/v1/invites/{token}")
    assert r.status_code == 429


def test_revoked_invite_peek_404(auth_env, store):
    admin_id, admin_jwt = _seed_admin(store)
    token, invite_id = _mint_invite(admin_id)
    with _client() as client:
        assert client.delete(
            f"/api/v1/admin/users/invites/{invite_id}",
            headers={"Authorization": f"Bearer {admin_jwt}"},
        ).status_code == 200
        r = client.get(f"/api/v1/invites/{token}")
    assert r.status_code == 404


def test_expired_invite_peek_404(auth_env, store):
    admin_id, _ = _seed_admin(store)
    token, invite_id = _mint_invite(admin_id)
    store.invites[invite_id]["expires_at"] = datetime.now(timezone.utc) - timedelta(hours=1)
    with _client() as client:
        r = client.get(f"/api/v1/invites/{token}")
    assert r.status_code == 404
