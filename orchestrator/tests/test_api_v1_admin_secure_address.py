# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Routes for the secure address at home (LUM-331): auth, status codes, shapes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytest_plugins = ("tests.test_auth_phase1",)

ORIGIN = "http://localhost"


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_PATH", str(tmp_path / "ws"))
    monkeypatch.setenv("CADDY_HOME_DIR", str(tmp_path / "caddy-home"))
    monkeypatch.setenv("LUMOGIS_PUBLIC_ORIGIN", ORIGIN)
    from services.home_dns import service

    # Routes start background work; keep it inert here (the service has its own tests).
    monkeypatch.setattr(service, "_advance", lambda generation: None)
    monkeypatch.setattr(service, "ensure_fast_job", lambda: None)
    monkeypatch.setattr(service, "refresh_when_free", lambda: None)
    monkeypatch.setattr(service, "after_disable", lambda: None)
    monkeypatch.setattr(service, "audit", lambda *a, **k: None)
    return tmp_path


def _client() -> TestClient:
    import main

    return TestClient(main.app)


def _tokens(users_store) -> tuple[str, str]:
    import services.users as users_svc
    from auth import mint_access_token

    admin = users_svc.create_user("admin@home.lan", "verylongpassword12", "admin")
    member = users_svc.create_user("member@home.lan", "verylongpassword12", "user")
    return mint_access_token(admin.id, "admin"), mint_access_token(member.id, "user")


def _h(token: str, origin: bool = False) -> dict[str, str]:
    h = {"Authorization": f"Bearer {token}"}
    if origin:
        h["Origin"] = ORIGIN
    return h


ADMIN_ROUTES = [
    ("GET", "/api/v1/admin/secure-address", None),
    ("POST", "/api/v1/admin/secure-address/enable", {"lan_address": "192.168.1.20"}),
    ("PUT", "/api/v1/admin/secure-address/lan-address", {"lan_address": "192.168.1.20"}),
    ("POST", "/api/v1/admin/secure-address/retry", None),
    ("POST", "/api/v1/admin/secure-address/detect", None),
    ("POST", "/api/v1/admin/secure-address/disable", None),
]


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
def test_admin_routes_401_anonymous(users_store, auth_env, ws, method, path, body):
    resp = _client().request(method, path, json=body)
    assert resp.status_code == 401


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
def test_admin_routes_403_member(users_store, auth_env, ws, method, path, body):
    _, member = _tokens(users_store)
    resp = _client().request(method, path, json=body, headers=_h(member))
    assert resp.status_code == 403


def test_get_status_off_and_no_store(users_store, auth_env, ws):
    admin, _ = _tokens(users_store)
    resp = _client().get(
        "/api/v1/admin/secure-address", headers={**_h(admin), "Host": "192.168.1.20"}
    )
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    body = resp.json()
    assert body["state"] == "off"
    assert body["suggested_address"] == "192.168.1.20"
    assert body["sign_in_enabled"] is True
    assert "bearer" not in resp.text


@pytest.mark.parametrize("address", ["8.8.8.8", "100.64.0.1", "localhost", "192.168.1.256"])
def test_enable_rejects_non_home_address(users_store, auth_env, ws, address):
    admin, _ = _tokens(users_store)
    resp = _client().post(
        "/api/v1/admin/secure-address/enable", json={"lan_address": address}, headers=_h(admin)
    )
    assert resp.status_code == 400


def test_enable_requires_address(users_store, auth_env, ws):
    admin, _ = _tokens(users_store)
    resp = _client().post("/api/v1/admin/secure-address/enable", json={}, headers=_h(admin))
    assert resp.status_code == 422


def test_enable_then_twice_409_then_disable(users_store, auth_env, ws):
    admin, _ = _tokens(users_store)
    c = _client()
    first = c.post(
        "/api/v1/admin/secure-address/enable",
        json={"lan_address": "192.168.1.20"},
        headers=_h(admin),
    )
    assert first.status_code == 202, first.text
    assert first.json()["state"] == "registering"
    again = c.post(
        "/api/v1/admin/secure-address/enable",
        json={"lan_address": "192.168.1.20"},
        headers=_h(admin),
    )
    assert again.status_code == 409
    assert (
        c.put(
            "/api/v1/admin/secure-address/lan-address",
            json={"lan_address": "10.0.0.7"},
            headers=_h(admin),
        ).json()["lan_address"]
        == "10.0.0.7"
    )
    assert c.post("/api/v1/admin/secure-address/retry", headers=_h(admin)).status_code == 202
    off = c.post("/api/v1/admin/secure-address/disable", headers=_h(admin))
    assert off.status_code == 200 and off.json()["state"] == "off"


def test_lan_address_rejects_non_home_address(users_store, auth_env, ws):
    admin, _ = _tokens(users_store)
    c = _client()
    c.post(
        "/api/v1/admin/secure-address/enable",
        json={"lan_address": "192.168.1.20"},
        headers=_h(admin),
    )
    resp = c.put(
        "/api/v1/admin/secure-address/lan-address",
        json={"lan_address": "8.8.8.8"},
        headers=_h(admin),
    )
    assert resp.status_code == 400


def test_lan_address_and_retry_409_when_off(users_store, auth_env, ws):
    admin, _ = _tokens(users_store)
    c = _client()
    assert (
        c.put(
            "/api/v1/admin/secure-address/lan-address",
            json={"lan_address": "10.0.0.7"},
            headers=_h(admin),
        ).status_code
        == 409
    )
    assert c.post("/api/v1/admin/secure-address/retry", headers=_h(admin)).status_code == 409


def test_busy_is_409(users_store, auth_env, ws, monkeypatch):
    from services.home_dns import service

    admin, _ = _tokens(users_store)
    monkeypatch.setattr(service, "ROUTE_LOCK_TIMEOUT_SEC", 0.05)
    service._lock.acquire()
    try:
        resp = _client().post("/api/v1/admin/secure-address/disable", headers=_h(admin))
    finally:
        service._lock.release()
    assert resp.status_code == 409
    assert resp.json()["detail"] == "busy"


def test_member_public_view(users_store, auth_env, ws):
    _, member = _tokens(users_store)
    resp = _client().get("/api/v1/secure-address", headers=_h(member))
    assert resp.status_code == 200
    assert resp.json() == {"url": None}
    assert resp.headers["cache-control"] == "no-store"
    assert _client().get("/api/v1/secure-address").status_code == 401


def test_stack_status_carries_secure_address(monkeypatch, ws):
    from services import stack_status as svc

    monkeypatch.setattr(svc, "_get_stack_control_payload", lambda: (None, False, None))
    monkeypatch.setattr(svc.stack_control, "token", lambda: "secret")
    monkeypatch.setattr(svc, "_build_store_pings", lambda: {})
    monkeypatch.setattr(svc, "_ollama_read_only_block", lambda: ([], []))
    resp = svc.build_stack_status_response()
    assert resp.secure_address is not None
    assert resp.secure_address.state == "off" and resp.secure_address.url is None


def test_concurrent_enable_serialised(users_store, auth_env, ws):
    """Two admins press Turn on at once: one 202, one 409, one generation."""
    import threading

    from services.home_dns import state as st

    admin, _ = _tokens(users_store)
    barrier = threading.Barrier(2)
    codes: list[int] = []

    def press() -> None:
        client = _client()
        barrier.wait()
        resp = client.post(
            "/api/v1/admin/secure-address/enable",
            json={"lan_address": "192.168.1.20"},
            headers=_h(admin),
        )
        codes.append(resp.status_code)

    threads = [threading.Thread(target=press) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [202, 409]
    assert st.load().generation == 1


def test_core_starts_with_feature_off_and_worker_unreachable(tmp_path, monkeypatch):
    """App start-up with the name service unreachable: health is fine, state off."""
    import main
    from services.home_dns import service
    from services.home_dns import worker_client

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("WORKSPACE_PATH", str(tmp_path / "ws"))
    monkeypatch.setenv("CADDY_HOME_DIR", str(tmp_path / "caddy-home"))
    monkeypatch.setenv("LUMOGIS_HOME_DNS_URL", "https://127.0.0.1:1")

    def no_network(*a, **k):
        raise AssertionError("the name service must not be contacted while off")

    monkeypatch.setattr(worker_client.httpx, "request", no_network)
    import config

    scheduled: list[str] = []
    sched = config.get_scheduler()  # the conftest stand-in: record what start-up adds
    real_add = sched.add_job
    monkeypatch.setattr(
        sched, "add_job", lambda *a, **kw: (scheduled.append(kw.get("id")), real_add(*a, **kw))[1]
    )
    with TestClient(main.app) as c:
        assert service.SLOW_JOB_ID in scheduled
        assert c.get("/api/v1/health").status_code == 200
        service.refresh_job()  # the upkeep job, run once, off: no Worker call
        assert c.get("/api/v1/admin/secure-address").json()["state"] == "off"


# --------------------------------------------------------------------------- LUM-996


def test_detect_returns_status_with_the_detected_suggestion(users_store, auth_env, ws, monkeypatch):
    from services.home_dns import service

    admin, _ = _tokens(users_store)
    monkeypatch.setattr(service, "detect_lan_address", lambda timeout=None: ("10.0.0.5", None))
    monkeypatch.setattr(service, "_last_detect_done", float("-inf"))
    resp = _client().post(
        "/api/v1/admin/secure-address/detect",
        headers={**_h(admin, origin=True), "Host": "localhost"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["cache-control"] == "no-store"
    assert resp.json()["suggested_address"] == "10.0.0.5"


def test_detect_busy_is_409(users_store, auth_env, ws, monkeypatch):
    from services.home_dns import service

    def busy():
        raise service.Busy()

    admin, _ = _tokens(users_store)
    monkeypatch.setattr(service, "detect_now", busy)
    resp = _client().post("/api/v1/admin/secure-address/detect", headers=_h(admin, origin=True))
    assert resp.status_code == 409 and resp.json()["detail"] == "busy"


def test_get_never_runs_detection(users_store, auth_env, ws, monkeypatch):
    from services.home_dns import service

    def boom(*a, **k):
        raise AssertionError("GET must not detect")

    admin, _ = _tokens(users_store)
    monkeypatch.setattr(service, "detect_lan_address", boom)
    monkeypatch.setattr(service, "detect_now", boom)
    assert _client().get("/api/v1/admin/secure-address", headers=_h(admin)).status_code == 200


def test_retry_queues_a_refresh(users_store, auth_env, ws, monkeypatch):
    from services.home_dns import service

    order: list[str] = []
    monkeypatch.setattr(service, "_advance", lambda generation: order.append("advance"))
    monkeypatch.setattr(service, "refresh_when_free", lambda: order.append("refresh"))
    admin, _ = _tokens(users_store)
    c = _client()
    c.post(
        "/api/v1/admin/secure-address/enable",
        json={"lan_address": "192.168.1.20"},
        headers=_h(admin),
    )
    order.clear()
    assert c.post("/api/v1/admin/secure-address/retry", headers=_h(admin)).status_code == 202
    assert order == ["advance", "refresh"]


def test_status_carries_name_new_until(users_store, auth_env, ws):
    from datetime import datetime
    from datetime import timedelta
    from datetime import timezone

    from services.home_dns import state as st

    now = datetime.now(timezone.utc)
    st.save(
        st.HomeDnsState(
            enabled=True,
            household_id="kq7f3r2abcdefghijklmnopqrs",
            bearer="b" * 43,
            lan_address="192.168.1.20",
            registered_at=now - timedelta(minutes=5),
            name_published_at=now - timedelta(minutes=5),
            snippet_applied_at=now - timedelta(minutes=4),
            cert_not_before=now - timedelta(days=1),
            cert_not_after=now + timedelta(days=89),
        )
    )
    admin, _ = _tokens(users_store)
    body = _client().get("/api/v1/admin/secure-address", headers=_h(admin)).json()
    assert body["state"] == "ready" and body["name_new_until"] is not None
