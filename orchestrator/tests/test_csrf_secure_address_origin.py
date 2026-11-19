# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Cross-site check on secure-address hosts (``*.homes.lumogis.app``, LUM-331).

Households share the registrable domain ``lumogis.app`` (no Public Suffix List
entry), so a sibling household's page is *same-site* and ``SameSite=Strict``
cookies do not separate them. On a secure-address host the Origin must equal
``https://`` + Host, whatever ``LUMOGIS_PUBLIC_ORIGIN`` says. Tests use the
compose default ``LUMOGIS_PUBLIC_ORIGIN=http://localhost`` (``docker-compose.yml``)
because "public origin unset" never happens on a compose install.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytest_plugins = ("tests.test_auth_phase1",)

HID = "kq7f3r2abcdefghijklmnopqrs"
SECURE = f"{HID}.homes.lumogis.app"
SIBLING = "https://zz7f3r2abcdefghijklmnopqrs.homes.lumogis.app"


@pytest.fixture(params=["http://localhost", "http://192.168.1.20"])
def compose_origin(request, monkeypatch) -> str:
    monkeypatch.setenv("LUMOGIS_PUBLIC_ORIGIN", request.param)
    monkeypatch.delenv("LUMOGIS_HOME_DNS_ZONE", raising=False)
    return request.param


def _client(base: str) -> TestClient:
    import main

    return TestClient(main.app, base_url=base)


def _seed_and_login(users_store, client: TestClient, origin: str) -> None:
    import services.users as users_svc

    users_svc.create_user("admin@home.lan", "verylongpassword12", "admin")
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@home.lan", "password": "verylongpassword12"},
        headers={"Origin": origin},
    )
    assert resp.status_code == 200, resp.text


def _refresh(client: TestClient, headers: dict[str, str]):
    return client.post("/api/v1/auth/refresh", headers=headers)


def test_secure_host_matching_origin_passes(users_store, auth_env, compose_origin):
    client = _client(f"https://{SECURE}")
    _seed_and_login(users_store, client, f"https://{SECURE}")
    assert _refresh(client, {"Origin": f"https://{SECURE}"}).status_code == 200


@pytest.mark.parametrize(
    "origin",
    [SIBLING, None, "null", "http://localhost", "http://192.168.1.20", f"http://{SECURE}"],
)
def test_secure_host_other_origins_refused(users_store, auth_env, compose_origin, origin):
    client = _client(f"https://{SECURE}")
    _seed_and_login(users_store, client, f"https://{SECURE}")
    headers = {} if origin is None else {"Origin": origin}
    resp = _refresh(client, headers)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "origin mismatch"


def test_secure_host_with_port(users_store, auth_env, compose_origin):
    client = _client(f"https://{SECURE}:8443")
    _seed_and_login(users_store, client, f"https://{SECURE}:8443")
    assert _refresh(client, {"Origin": f"https://{SECURE}"}).status_code == 403
    assert _refresh(client, {"Origin": f"https://{SECURE}:8443"}).status_code == 200


def test_secure_host_mixed_case_host_normalised(users_store, auth_env, compose_origin):
    client = _client(f"https://{SECURE}")
    _seed_and_login(users_store, client, f"https://{SECURE}")
    resp = _refresh(client, {"Host": SECURE.upper(), "Origin": f"https://{SECURE}"})
    assert resp.status_code == 200, resp.text


def test_secure_host_bearer_bypasses(users_store, auth_env, compose_origin):
    """Lumogis Search refreshes with Bearer + cookie; Bearer is not a CSRF surface."""
    client = _client(f"https://{SECURE}")
    _seed_and_login(users_store, client, f"https://{SECURE}")
    resp = _refresh(client, {"Origin": SIBLING, "Authorization": "Bearer anything"})
    assert resp.status_code == 200, resp.text


def test_lan_host_unchanged(users_store, auth_env, compose_origin):
    """A non-zone host keeps today's rule: Origin must equal LUMOGIS_PUBLIC_ORIGIN."""
    client = _client(compose_origin)
    _seed_and_login(users_store, client, compose_origin)
    assert _refresh(client, {"Origin": f"https://{SECURE}"}).status_code == 403
    assert _refresh(client, {"Origin": compose_origin}).status_code == 200


def test_logout_on_secure_host_needs_matching_origin(users_store, auth_env, compose_origin):
    client = _client(f"https://{SECURE}")
    _seed_and_login(users_store, client, f"https://{SECURE}")
    assert client.post("/api/v1/auth/logout", headers={"Origin": SIBLING}).status_code == 403
    ok = client.post("/api/v1/auth/logout", headers={"Origin": f"https://{SECURE}"})
    assert ok.status_code == 200
    assert ok.json() == {"ok": True}


def test_logout_on_lan_host_with_public_origin(users_store, auth_env, compose_origin):
    client = _client(compose_origin)
    _seed_and_login(users_store, client, compose_origin)
    assert client.post("/api/v1/auth/logout", headers={"Origin": compose_origin}).status_code == 200


def test_auth_off_bypasses_even_on_secure_host(monkeypatch):
    from csrf import require_same_origin
    from starlette.requests import Request

    monkeypatch.setenv("AUTH_ENABLED", "false")
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/x",
        "headers": [(b"host", SECURE.encode()), (b"origin", SIBLING.encode())],
    }
    assert require_same_origin(Request(scope)) is None
