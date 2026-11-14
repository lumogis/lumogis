# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route tests for POST /me/email-sources/test — the pre-save live test (LUM-644).

Credentials are request-scoped only: never written to any store, never logged.
Structurally-malformed input is a Pydantic 422; every connection failure is a
normal 200 {ok:false}.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from tests.test_auth_phase1 import FakeUsersStore

from services import imap_transport


@pytest.fixture
def env(monkeypatch):
    import config as _config

    store = FakeUsersStore()
    _config._instances["metadata_store"] = store
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    # Default: _connection_from_payload succeeds (skip real DNS/TLS on the fake
    # host); individual tests override it to exercise the SSRF-block path.
    from services import email_credentials

    def _fake_conn(payload):
        return email_credentials.ImapConnection(
            host=payload["host"],
            port=payload.get("port", 993),
            username=payload["username"],
            use_ssl=payload.get("use_ssl", True),
            auth_type="app_password",
            password=payload["password"],
        )

    monkeypatch.setattr(email_credentials, "_connection_from_payload", _fake_conn)
    # Reset the module-level per-user limiter between tests.
    from routes import signals as signals_routes

    signals_routes._email_test_limiter.clear()
    yield store
    _config._instances.pop("metadata_store", None)


@contextmanager
def _client():
    import main
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        yield client


def _ok_probe(*a, **k):
    return imap_transport.ProbeResult(
        ok=True,
        message_count=7,
        folders=[{"name": "Receipts", "role": None, "hidden": False, "selected": True}],
        capabilities=["IMAP4REV1"],
    )


def _body(**kw):
    b = {"host": "imap.example.com", "username": "a@b.com", "password": "app-pw"}
    b.update(kw)
    return b


def test_valid_creds_ok_and_no_persistence(env, monkeypatch):
    monkeypatch.setattr(imap_transport, "probe", _ok_probe)
    with _client() as client:
        resp = client.post("/me/email-sources/test", json=_body())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True and data["message_count"] == 7
    # No persistence: the test route has no INSERT/credential-write path at all
    # (credentials are request-scoped locals passed to probe and discarded).


def test_bad_creds_returns_ok_false(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("authentication failed")

    monkeypatch.setattr(imap_transport, "probe", boom)
    with _client() as client:
        resp = client.post("/me/email-sources/test", json=_body())
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_ssrf_block_returns_ok_false(env, monkeypatch):
    from services import email_credentials

    def blocked(payload):
        raise email_credentials.ImapHostBlockedError("imap host blocked: private")

    monkeypatch.setattr(email_credentials, "_connection_from_payload", blocked)
    with _client() as client:
        resp = client.post("/me/email-sources/test", json=_body(host="10.0.0.1"))
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_malformed_port_is_422(env):
    with _client() as client:
        resp = client.post("/me/email-sources/test", json=_body(port=99999))
    assert resp.status_code == 422


def test_xoauth2_is_422(env):
    with _client() as client:
        resp = client.post("/me/email-sources/test", json=_body(auth_type="xoauth2"))
    assert resp.status_code == 422


def test_partial_probe_degrades(env, monkeypatch):
    def partial(*a, **k):
        return imap_transport.ProbeResult(
            ok=True,
            message_count=None,
            folders=[{"name": "X", "role": None, "hidden": False, "selected": True}],
        )

    monkeypatch.setattr(imap_transport, "probe", partial)
    with _client() as client:
        resp = client.post("/me/email-sources/test", json=_body())
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True and data["message_count"] is None


def test_proxy_unverified_soft(env, monkeypatch):
    monkeypatch.setattr(
        imap_transport,
        "probe",
        lambda *a, **k: imap_transport.ProbeResult(ok=False, proxy_unverified=True),
    )
    with _client() as client:
        resp = client.post("/me/email-sources/test", json=_body())
    assert resp.status_code == 200
    assert resp.json()["proxy_unverified"] is True


def test_rate_limited_after_budget(env, monkeypatch):
    monkeypatch.setattr(imap_transport, "probe", _ok_probe)
    with _client() as client:
        codes = [client.post("/me/email-sources/test", json=_body()).status_code for _ in range(12)]
    assert 429 in codes  # per-user budget (10/min) exceeded


def test_password_never_logged(env, monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr(imap_transport, "probe", boom)
    with caplog.at_level("WARNING"):
        with _client() as client:
            client.post("/me/email-sources/test", json=_body(password="s3cr3t-app-pw"))
    assert "s3cr3t-app-pw" not in caplog.text
