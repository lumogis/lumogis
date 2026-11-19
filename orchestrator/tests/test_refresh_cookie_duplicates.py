# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Duplicate ``lumogis_refresh`` cookies (LUM-331, decision F2).

Lumogis sets the refresh cookie host-only, so a browser never sends two of its
own. A second value is one planted for a parent domain by another site; on a
secure address that can be a sibling household. The server tries each value
(no count limit, so planted ones cannot crowd it out) and uses one that
verifies; logout revokes every verifying value.
The cookie name is unchanged because Lumogis Search parses it by name.
"""

from __future__ import annotations

import jwt
import pytest
from fastapi.testclient import TestClient

pytest_plugins = ("tests.test_auth_phase1",)

HID = "kq7f3r2abcdefghijklmnopqrs"
SECURE = f"{HID}.homes.lumogis.app"


@pytest.fixture
def no_public_origin(monkeypatch):
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)


def _client(base: str = "http://testserver") -> TestClient:
    import main

    return TestClient(main.app, base_url=base)


def _login(client: TestClient) -> str:
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@home.lan", "password": "verylongpassword12"},
    )
    assert resp.status_code == 200, resp.text
    value = client.cookies.get("lumogis_refresh")
    assert value
    return value


def _seed():
    import services.users as users_svc

    return users_svc.create_user("admin@home.lan", "verylongpassword12", "admin")


def _foreign_token(i: int = 0) -> str:
    """A refresh-shaped JWT signed with a key this box does not have."""
    return jwt.encode(
        {"sub": "someone-else", "jti": f"foreign-{i}", "typ": "refresh"},
        "a-different-households-secret",
        algorithm="HS256",
    )


def _post_with_cookie_header(path: str, values: list[str]):
    fresh = _client()  # empty cookie jar: only the raw header below is sent
    header = "; ".join(f"lumogis_refresh={v}" for v in values)
    return fresh.post(path, headers={"Cookie": header})


def test_refresh_uses_the_valid_second_value(users_store, auth_env, no_public_origin):
    _seed()
    valid = _login(_client())
    resp = _post_with_cookie_header("/api/v1/auth/refresh", [_foreign_token(), valid])
    assert resp.status_code == 200, resp.text
    assert "access_token" in resp.json()


def test_refresh_with_only_foreign_values_is_401(users_store, auth_env, no_public_origin):
    _seed()
    resp = _post_with_cookie_header("/api/v1/auth/refresh", [_foreign_token(1), _foreign_token(2)])
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid refresh token"


def test_refresh_without_cookie_is_401_missing(users_store, auth_env, no_public_origin):
    _seed()
    resp = _client().post("/api/v1/auth/refresh")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "missing refresh cookie"


def test_planted_values_cannot_crowd_out_the_real_one(users_store, auth_env, no_public_origin):
    """A sibling can plant many values with a longer Path, which browsers send first."""
    _seed()
    valid = _login(_client())
    planted = [_foreign_token(i) for i in range(40)]
    assert _post_with_cookie_header("/api/v1/auth/refresh", [*planted, valid]).status_code == 200


def test_logout_revokes_every_verifying_value(users_store, auth_env, no_public_origin):
    from services import auth_sessions as asn

    user = _seed()
    first = _login(_client())
    second = _login(_client())
    assert len(asn.list_active_sessions_for_user(user.id)) == 2
    resp = _post_with_cookie_header("/api/v1/auth/logout", [first, _foreign_token(), second])
    assert resp.status_code == 200
    assert asn.list_active_sessions_for_user(user.id) == []


def test_secure_host_cookie_always_secure(users_store, auth_env, monkeypatch):
    """auth_env sets LUMOGIS_REFRESH_COOKIE_SECURE=false (plain-HTTP LAN)."""
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    _seed()
    secure_client = _client(f"https://{SECURE}")
    resp = secure_client.post(
        "/api/v1/auth/login",
        json={"email": "admin@home.lan", "password": "verylongpassword12"},
        headers={"Origin": f"https://{SECURE}"},
    )
    assert resp.status_code == 200, resp.text
    assert "secure" in resp.headers["set-cookie"].lower()

    lan = _client("http://192.168.1.20")
    resp2 = lan.post(
        "/api/v1/auth/login",
        json={"email": "admin@home.lan", "password": "verylongpassword12"},
    )
    assert resp2.status_code == 200, resp2.text
    assert "secure" not in resp2.headers["set-cookie"].lower()
