# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``/api/v1/setup/*`` route contract (LUM-981: R1-R17, H4, C11).

TestClient against ``main.app`` (no lifespan), with ``instance_setup`` forced
open or closed through ``_reset_for_tests``. The users come from the Phase 1
``FakeUsersStore``; the module's SQL helpers (advisory lock, singleton row) are
replaced because that fake does not speak those statements. The race and the
rollback are proven against real Postgres in
``integration/test_instance_setup_live.py``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import psycopg2.errors
import pytest
import routes.setup as setup_routes
import services.instance_setup as setup
from fastapi.testclient import TestClient
from ports.metadata_store import PoolExhaustedError
from services.instance_setup import BootState

import config

# Register Phase-1 fakes + fixtures without importing fixture names.
pytest_plugins = ("tests.test_auth_phase1",)

EMAIL = "admin@example.com"
PASSWORD = "correct-horse-battery"
BODY = {"email": EMAIL, "password": PASSWORD}


@pytest.fixture(autouse=True)
def _isolated_setup_state(monkeypatch):
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    setup._reset_for_tests()
    setup_routes._reset_limiters_for_tests()
    yield
    setup._reset_for_tests()
    setup_routes._reset_limiters_for_tests()


@pytest.fixture
def sql_helpers(monkeypatch):
    """The fake users store cannot run the lock or singleton-row SQL."""
    rows = {"inserted": []}
    monkeypatch.setattr(setup, "_set_lock_timeout", lambda: None)
    monkeypatch.setattr(setup, "_lock", lambda: None)
    monkeypatch.setattr(setup, "_row_exists", lambda: False)

    def insert(user_id: str) -> bool:
        rows["inserted"].append(user_id)
        return True

    monkeypatch.setattr(setup, "_insert_row", insert)
    return rows


@pytest.fixture
def scheduler(monkeypatch):
    sched = MagicMock()
    monkeypatch.setattr(config, "get_scheduler", lambda: sched)
    return sched


@pytest.fixture
def remap(monkeypatch):
    called = MagicMock()
    monkeypatch.setattr(setup_routes, "_remap_after_claim", called)
    return called


@pytest.fixture
def client():
    import main

    return TestClient(main.app)


@pytest.fixture
def open_setup(users_store, auth_env, sql_helpers, scheduler, remap):
    setup._reset_for_tests(BootState.OPEN)
    return users_store


def _claim(client, body=None, **headers):
    return client.post("/api/v1/setup/claim", json=BODY if body is None else body, headers=headers)


# --- status -----------------------------------------------------------------


def test_r1_status_true_when_open(open_setup, client):
    r = client.get("/api/v1/setup/status")
    assert r.status_code == 200
    assert r.json() == {"needs_setup": True}
    assert r.headers["cache-control"] == "no-store"


def test_r2_status_false_when_closed(users_store, auth_env, client):
    r = client.get("/api/v1/setup/status")
    assert r.json() == {"needs_setup": False}
    assert r.headers["cache-control"] == "no-store"


def test_r3_status_false_with_sign_in_off(users_store, dev_env, client):
    setup._reset_for_tests(BootState.OPEN)
    assert client.get("/api/v1/setup/status").json() == {"needs_setup": False}


# --- claim: success and refusals --------------------------------------------


def test_r4_claim_creates_admin_signs_in_and_cleans_up(open_setup, client, remap, scheduler):
    r = _claim(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["role"] == "admin"
    assert body["user"]["email"] == EMAIL
    assert body["access_token"]
    assert "lumogis_refresh" in r.headers.get("set-cookie", "")
    assert len(open_setup.auth_sessions) == 1  # a session row was inserted
    remap.assert_called_once_with()  # background task ran after the response
    scheduler.remove_job.assert_called_once_with("setup_claim_reminder")
    assert setup.is_open() is False


def test_r5_second_claim_is_409(open_setup, client):
    assert _claim(client).status_code == 200
    r = _claim(client, {"email": "other@example.com", "password": PASSWORD})
    assert r.status_code == 409
    assert r.json() == {"detail": "setup is not open"}


def test_r6_claim_with_sign_in_off_is_409(users_store, dev_env, client):
    setup._reset_for_tests(BootState.OPEN)
    r = _claim(client)
    assert r.status_code == 409
    assert r.json() == {"detail": "setup is not open"}


def test_r7_short_password_is_422_with_a_string_detail(open_setup, client):
    r = _claim(client, {"email": EMAIL, "password": "short"})
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str)
    assert "at least" in r.json()["detail"]
    assert open_setup.rows == {}


def test_r8_bad_email_is_422(open_setup, client):
    r = _claim(client, {"email": "not-an-email", "password": PASSWORD})
    assert r.status_code == 422
    assert open_setup.rows == {}


def test_r9_missing_field_is_422(open_setup, client):
    assert _claim(client, {"email": EMAIL}).status_code == 422
    assert _claim(client, {"password": PASSWORD}).status_code == 422
    assert open_setup.rows == {}


def test_r10_five_failures_then_429_with_retry_after(open_setup, client):
    for _ in range(5):
        assert _claim(client, {"email": EMAIL, "password": "short"}).status_code == 422
    r = _claim(client, BODY)
    assert r.status_code == 429
    assert r.json() == {"detail": "too many failed attempts; try again in a minute"}
    assert r.headers["retry-after"] == "60"
    assert open_setup.rows == {}


def test_r16_lock_timeout_is_503_database_unavailable(open_setup, client, monkeypatch):
    def timeout():
        raise psycopg2.errors.LockNotAvailable("canceling statement due to lock timeout")

    monkeypatch.setattr(setup, "_lock", timeout)
    r = _claim(client)
    assert r.status_code == 503
    assert r.json() == {"detail": "database_unavailable"}
    assert setup.is_open() is True


def test_r16_pool_exhausted_is_503_database_unavailable(open_setup, client, monkeypatch):
    def exhausted():
        raise PoolExhaustedError("no connection")

    monkeypatch.setattr(setup, "_lock", exhausted)
    r = _claim(client)
    assert r.status_code == 503
    assert r.json() == {"detail": "database_unavailable"}


def test_r16_an_unrelated_error_is_not_swallowed_into_503(open_setup, monkeypatch):
    import main

    def boom():
        raise RuntimeError("bug")

    monkeypatch.setattr(setup, "_lock", boom)
    c = TestClient(main.app, raise_server_exceptions=False)
    assert _claim(c).status_code == 500


def test_r17_session_failure_after_claim_is_500_and_setup_stays_closed(open_setup, monkeypatch):
    import main

    def boom(*_a, **_k):
        raise RuntimeError("mint failed")

    monkeypatch.setattr(setup_routes, "_start_session", boom)
    c = TestClient(main.app, raise_server_exceptions=False)
    assert _claim(c).status_code == 500
    assert setup.is_open() is False
    assert len(open_setup.rows) == 1  # the admin exists
    r = _claim(c, {"email": "other@example.com", "password": PASSWORD})
    assert r.status_code == 409


# --- bypass and /auth/me ----------------------------------------------------


def test_r12_setup_status_is_public_but_a_near_miss_prefix_is_not(open_setup, client):
    assert client.get("/api/v1/setup/status").status_code == 200  # no Bearer, sign-in on
    assert client.get("/api/v1/setupx").status_code == 401


def test_r13_auth_me_stays_401_while_setup_is_open(open_setup, client):
    assert client.get("/api/v1/auth/me").status_code == 401


# --- DNS rebinding: the Host guard ------------------------------------------


def test_r14_rebound_host_is_refused_before_anything_is_created(open_setup, client):
    r = _claim(client, Host="evil.example", Origin="http://evil.example")
    assert r.status_code == 403
    assert r.json() == {"detail": "setup_host_not_allowed"}
    assert open_setup.rows == {}
    assert setup.is_open() is True


def test_r14b_a_junk_bearer_does_not_skip_the_host_guard(open_setup, client):
    r = _claim(
        client,
        Host="evil.example",
        Origin="http://evil.example",
        Authorization="Bearer junk",
    )
    assert r.status_code == 403
    assert r.json() == {"detail": "setup_host_not_allowed"}
    assert open_setup.rows == {}


@pytest.mark.parametrize(
    ("host", "origin"),
    [
        ("192.168.1.20", "http://192.168.1.20"),
        ("[fd00::1]:80", "http://[fd00::1]"),
        ("localhost:8090", "http://localhost:8090"),
        ("nas", "http://nas"),
        ("lumogis.lan", "http://lumogis.lan"),
        ("box.home.arpa", "http://box.home.arpa"),
        ("lumogis.tail1234.ts.net", "http://lumogis.tail1234.ts.net"),
        # A TLS terminator (Tailscale Serve) in front of Caddy: Caddy reports http,
        # the browser's Origin is https for the same host.
        ("lumogis.tail1234.ts.net", "https://lumogis.tail1234.ts.net"),
        ("lumogis-lan.test", "http://lumogis-lan.test"),
    ],
)
def test_r15_allowed_hosts_complete_the_claim(open_setup, client, host, origin):
    r = _claim(client, Host=host, Origin=origin)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["role"] == "admin"


def test_r15_a_pinned_public_origin_completes_the_claim(open_setup, client, monkeypatch):
    monkeypatch.setenv("LUMOGIS_PUBLIC_ORIGIN", "https://lumogis.example.com")
    r = _claim(client, Host="lumogis.example.com", Origin="https://lumogis.example.com")
    assert r.status_code == 200, r.text


def test_h4_a_public_host_with_an_invalid_body_is_403_not_422(open_setup, client):
    r = _claim(client, {"email": "nope"}, Host="evil.example", Origin="http://evil.example")
    assert r.status_code == 403
    assert r.json() == {"detail": "setup_host_not_allowed"}


def test_c11_foreign_origin_on_an_allowed_host_is_403_origin_mismatch(open_setup, client):
    r = _claim(client, Host="lumogis.lan", Origin="http://evil.example")
    assert r.status_code == 403
    assert r.json() == {"detail": "origin mismatch"}
    assert open_setup.rows == {}
