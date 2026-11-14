# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route tests for the OAuth device-flow connect routes (LUM-687 / 674b).

POST /me/email-sources/oauth/{provider}/device/{start,poll}. Mocks the
email_oauth state machine + imap_transport.probe + the credential store; asserts
the live-verify-then-persist (credential-first, soft-save on proxy) contract and
that no token reaches the wire.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from tests.test_auth_phase1 import FakeUsersStore

from services import connector_credentials as ccs
from services import email_oauth
from services import imap_transport
from services import smtp_transport


class _FakeStore(FakeUsersStore):
    def __init__(self):
        super().__init__()
        self.inserts: list = []
        self.fail_insert = False

    def execute(self, q, params=None):
        if "insert into sources" in (q or "").lower():
            if self.fail_insert:
                raise RuntimeError("insert boom")
            self.inserts.append(params)
            return None
        return super().execute(q, params) if hasattr(super(), "execute") else None


@pytest.fixture
def env(monkeypatch):
    import config as _config

    store = _FakeStore()
    _config._instances["metadata_store"] = store
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    monkeypatch.setenv("LUMOGIS_OAUTH_MICROSOFT_CLIENT_ID", "cid-ms")

    from routes import signals as sig

    sig._oauth_start_limiter.clear()
    sig._oauth_poll_limiter.clear()

    # No real scheduling.
    import signals.feed_monitor as fm

    monkeypatch.setattr(fm, "schedule_source", lambda *a, **k: None)

    # LUM-693 — the poll route's best-effort SMTP verify defaults to "ok" so
    # existing authorized-path tests don't attempt a real network connect; tests
    # that care about the SMTP-verify outcome override this explicitly.
    monkeypatch.setattr(smtp_transport, "verify_smtp_connection", lambda **kw: (True, True, None))

    # put_payload / delete_payload spies.
    puts: list = []
    deletes: list = []
    monkeypatch.setattr(
        ccs,
        "put_payload",
        lambda u, c, p, *, actor, instance="": puts.append((c, actor, instance, p)),
    )
    monkeypatch.setattr(
        ccs, "delete_payload", lambda u, c, *, actor, instance="": deletes.append((c, instance))
    )

    yield store, puts, deletes
    _config._instances.pop("metadata_store", None)


@contextmanager
def _client():
    import main
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        yield client


_FLOW = {
    "provider": "microsoft",
    "email": "a@b.com",
    "mailbox_label": "work",
    "host": "outlook.office365.com",
    "port": 993,
    "smtp_host": "smtp.office365.com",
    "smtp_port": 587,
    # LUM-691 — import-scope parity fields carried on the flow record.
    "import_scope": "last_30d",
    "import_since": None,
    "import_before": None,
}


def _authorized_poll(access="AT"):
    return email_oauth.DevicePoll(
        status="authorized", refresh_token="RT", access_token=access, expiry=9999999999.0
    )


# --- start ---------------------------------------------------------------- #


def test_start_returns_flow_fields(env, monkeypatch):
    monkeypatch.setattr(
        email_oauth,
        "start_device_flow",
        lambda *a, **k: email_oauth.DeviceFlow("FID", "ABCD", "https://ms.com/dev", None, 5, 900),
    )
    with _client() as client:
        resp = client.post(
            "/me/email-sources/oauth/outlook/device/start",
            json={"email": "a@b.com", "mailbox_label": "work"},
        )
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["flow_id"] == "FID" and d["user_code"] == "ABCD" and d["interval"] == 5


def test_start_unsupported_provider_422(env):
    with _client() as client:
        for prov in ("icloud", "gmail", "nope"):
            resp = client.post(
                f"/me/email-sources/oauth/{prov}/device/start",
                json={"email": "a@b.com", "mailbox_label": "work"},
            )
            assert resp.status_code == 422, prov


def test_start_no_client_id_503(env, monkeypatch):
    monkeypatch.delenv("LUMOGIS_OAUTH_MICROSOFT_CLIENT_ID", raising=False)
    with _client() as client:
        resp = client.post(
            "/me/email-sources/oauth/outlook/device/start",
            json={"email": "a@b.com", "mailbox_label": "work"},
        )
    assert resp.status_code == 503


def test_start_bad_email_422(env):
    with _client() as client:
        resp = client.post(
            "/me/email-sources/oauth/outlook/device/start",
            json={"email": "not-an-email", "mailbox_label": "work"},
        )
    assert resp.status_code == 422


# LUM-691 — import-scope parity with the app-password EmailSourceRequest path.
def test_start_forwards_import_scope_to_the_flow(env, monkeypatch):
    captured = {}

    def _start(uid, **kw):
        captured.update(kw)
        return email_oauth.DeviceFlow("FID", "ABCD", "https://ms.com/dev", None, 5, 900)

    monkeypatch.setattr(email_oauth, "start_device_flow", _start)
    with _client() as client:
        resp = client.post(
            "/me/email-sources/oauth/outlook/device/start",
            json={
                "email": "a@b.com",
                "mailbox_label": "work",
                "import_scope": "custom",
                "import_since": "2026-01-01",
            },
        )
    assert resp.status_code == 200, resp.text
    assert captured["import_scope"] == "custom"
    assert str(captured["import_since"]) == "2026-01-01"
    assert captured["import_before"] is None


def test_start_custom_scope_without_since_is_422(env):
    with _client() as client:
        resp = client.post(
            "/me/email-sources/oauth/outlook/device/start",
            json={"email": "a@b.com", "mailbox_label": "work", "import_scope": "custom"},
        )
    assert resp.status_code == 422


def test_start_non_custom_scope_with_dates_is_422(env):
    with _client() as client:
        resp = client.post(
            "/me/email-sources/oauth/outlook/device/start",
            json={
                "email": "a@b.com",
                "mailbox_label": "work",
                "import_scope": "last_7d",
                "import_since": "2026-01-01",
            },
        )
    assert resp.status_code == 422


# --- poll ----------------------------------------------------------------- #


def test_poll_authorized_verify_ok_persists_credential_first(env, monkeypatch):
    store, puts, deletes = env
    monkeypatch.setattr(email_oauth, "poll_device_flow", lambda u, f: _authorized_poll())
    monkeypatch.setattr(email_oauth, "get_flow", lambda u, f: dict(_FLOW))
    monkeypatch.setattr(email_oauth, "discard_flow", lambda f: None)
    monkeypatch.setattr(
        imap_transport, "probe", lambda conn, **k: imap_transport.ProbeResult(ok=True)
    )
    with _client() as client:
        resp = client.post("/me/email-sources/oauth/outlook/device/poll", json={"flow_id": "F"})
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["status"] == "connected" and d["verified"] is True and d["source_id"]
    # No token anywhere in the response.
    assert "RT" not in resp.text and "AT" not in resp.text
    # Credential-first: two put_payloads (IMAP then SMTP) BEFORE the sources INSERT.
    assert [c for c, *_ in puts] == ["imap", "smtp"]
    assert puts[0][3]["refresh_token"] == "RT" and puts[0][3]["access_token"] == "AT"
    assert puts[0][3]["username"] == "a@b.com" and puts[0][3]["provider"] == "microsoft"
    assert len(store.inserts) == 1 and deletes == []


# LUM-691 — the poll route's `sources` INSERT uses the FLOW's import_scope/
# since/before (set at start), not the old hardcoded "last_30d", None, None.
def test_poll_authorized_persists_the_flows_import_scope(env, monkeypatch):
    import datetime

    store, _, _ = env
    custom_flow = dict(_FLOW)
    custom_flow.update(
        import_scope="custom",
        import_since=datetime.date(2026, 1, 1),
        import_before=datetime.date(2026, 6, 1),
    )
    monkeypatch.setattr(email_oauth, "poll_device_flow", lambda u, f: _authorized_poll())
    monkeypatch.setattr(email_oauth, "get_flow", lambda u, f: custom_flow)
    monkeypatch.setattr(email_oauth, "discard_flow", lambda f: None)
    monkeypatch.setattr(
        imap_transport, "probe", lambda conn, **k: imap_transport.ProbeResult(ok=True)
    )
    with _client() as client:
        resp = client.post("/me/email-sources/oauth/outlook/device/poll", json={"flow_id": "F"})
    assert resp.status_code == 200, resp.text
    inserted = store.inserts[0]
    assert inserted[6:9] == ("custom", datetime.date(2026, 1, 1), datetime.date(2026, 6, 1))


# LUM-693 — the poll route's best-effort xoauth2 SMTP connect-test: it's called
# with the poll's access_token, and a hard SMTP failure does NOT block the
# already IMAP-verified mailbox from connecting (only logged).
def test_poll_authorized_calls_smtp_verify_with_xoauth2_and_access_token(env, monkeypatch):
    monkeypatch.setattr(email_oauth, "poll_device_flow", lambda u, f: _authorized_poll())
    monkeypatch.setattr(email_oauth, "get_flow", lambda u, f: dict(_FLOW))
    monkeypatch.setattr(email_oauth, "discard_flow", lambda f: None)
    monkeypatch.setattr(
        imap_transport, "probe", lambda conn, **k: imap_transport.ProbeResult(ok=True)
    )
    captured = {}

    def _verify(**kw):
        captured.update(kw)
        return (True, True, None)

    monkeypatch.setattr(smtp_transport, "verify_smtp_connection", _verify)
    with _client() as client:
        resp = client.post("/me/email-sources/oauth/outlook/device/poll", json={"flow_id": "F"})
    assert resp.status_code == 200, resp.text
    assert captured["auth_type"] == "xoauth2" and captured["access_token"] == "AT"
    assert captured["host"] == _FLOW["smtp_host"] and captured["username"] == _FLOW["email"]


def test_poll_authorized_smtp_verify_hard_failure_does_not_block_connect(env, monkeypatch):
    store, puts, _ = env
    monkeypatch.setattr(email_oauth, "poll_device_flow", lambda u, f: _authorized_poll())
    monkeypatch.setattr(email_oauth, "get_flow", lambda u, f: dict(_FLOW))
    monkeypatch.setattr(email_oauth, "discard_flow", lambda f: None)
    monkeypatch.setattr(
        imap_transport, "probe", lambda conn, **k: imap_transport.ProbeResult(ok=True)
    )
    monkeypatch.setattr(
        smtp_transport, "verify_smtp_connection", lambda **kw: (False, True, "smtp_auth")
    )
    with _client() as client:
        resp = client.post("/me/email-sources/oauth/outlook/device/poll", json={"flow_id": "F"})
    d = resp.json()
    # The mailbox still connects (IMAP already proved the account works) —
    # an SMTP send-scope failure is a logged, non-blocking secondary concern.
    assert d["status"] == "connected" and d["verified"] is True
    assert len(puts) == 2 and len(store.inserts) == 1


def test_poll_authorized_proxy_unverified_soft_saves(env, monkeypatch):
    store, puts, _ = env
    monkeypatch.setattr(email_oauth, "poll_device_flow", lambda u, f: _authorized_poll())
    monkeypatch.setattr(email_oauth, "get_flow", lambda u, f: dict(_FLOW))
    monkeypatch.setattr(email_oauth, "discard_flow", lambda f: None)
    monkeypatch.setattr(
        imap_transport,
        "probe",
        lambda conn, **k: imap_transport.ProbeResult(ok=False, proxy_unverified=True),
    )
    with _client() as client:
        resp = client.post("/me/email-sources/oauth/outlook/device/poll", json={"flow_id": "F"})
    d = resp.json()
    assert d["status"] == "connected" and d["verified"] is False
    assert len(puts) == 2 and len(store.inserts) == 1  # persisted (soft-save)


def test_poll_authorized_hard_verify_fail_persists_nothing(env, monkeypatch):
    store, puts, _ = env
    monkeypatch.setattr(email_oauth, "poll_device_flow", lambda u, f: _authorized_poll())
    monkeypatch.setattr(email_oauth, "get_flow", lambda u, f: dict(_FLOW))
    monkeypatch.setattr(email_oauth, "discard_flow", lambda f: None)
    monkeypatch.setattr(
        imap_transport,
        "probe",
        lambda conn, **k: imap_transport.ProbeResult(ok=False, proxy_unverified=False),
    )
    with _client() as client:
        resp = client.post("/me/email-sources/oauth/outlook/device/poll", json={"flow_id": "F"})
    d = resp.json()
    assert d["status"] == "error" and puts == [] and store.inserts == []


def test_poll_sources_insert_fail_compensates(env, monkeypatch):
    store, puts, deletes = env
    store.fail_insert = True
    monkeypatch.setattr(email_oauth, "poll_device_flow", lambda u, f: _authorized_poll())
    monkeypatch.setattr(email_oauth, "get_flow", lambda u, f: dict(_FLOW))
    monkeypatch.setattr(email_oauth, "discard_flow", lambda f: None)
    monkeypatch.setattr(
        imap_transport, "probe", lambda conn, **k: imap_transport.ProbeResult(ok=True)
    )
    with _client() as client:
        resp = client.post("/me/email-sources/oauth/outlook/device/poll", json={"flow_id": "F"})
    assert resp.status_code == 500
    # Both credentials compensated-deleted.
    assert {c for c, _ in deletes} == {"imap", "smtp"}


def test_poll_pending_writes_nothing(env, monkeypatch):
    store, puts, _ = env
    monkeypatch.setattr(
        email_oauth, "poll_device_flow", lambda u, f: email_oauth.DevicePoll(status="pending")
    )
    with _client() as client:
        resp = client.post("/me/email-sources/oauth/outlook/device/poll", json={"flow_id": "F"})
    assert resp.json()["status"] == "pending" and puts == [] and store.inserts == []


def test_poll_not_found_maps_to_expired(env, monkeypatch):
    monkeypatch.setattr(
        email_oauth, "poll_device_flow", lambda u, f: email_oauth.DevicePoll(status="not_found")
    )
    with _client() as client:
        resp = client.post("/me/email-sources/oauth/outlook/device/poll", json={"flow_id": "F"})
    assert resp.json()["status"] == "expired"


def test_poll_denied_passthrough(env, monkeypatch):
    monkeypatch.setattr(
        email_oauth, "poll_device_flow", lambda u, f: email_oauth.DevicePoll(status="denied")
    )
    with _client() as client:
        resp = client.post("/me/email-sources/oauth/outlook/device/poll", json={"flow_id": "F"})
    assert resp.json()["status"] == "denied"


def test_poll_at_cadence_does_not_429(env, monkeypatch):
    monkeypatch.setattr(
        email_oauth, "poll_device_flow", lambda u, f: email_oauth.DevicePoll(status="pending")
    )
    with _client() as client:
        # 12 polls in the window (5s cadence) must all succeed — poll limiter is 60/60.
        for _ in range(12):
            resp = client.post("/me/email-sources/oauth/outlook/device/poll", json={"flow_id": "F"})
            assert resp.status_code == 200


# --- provider list dynamic gating ----------------------------------------- #


def test_provider_list_outlook_enabled_when_configured(env):
    with _client() as client:
        resp = client.get("/api/v1/me/email-providers")
    outlook = next(p for p in resp.json()["providers"] if p["id"] == "outlook")
    assert outlook["enabled"] is True and outlook["supports_oauth"] is True
    assert outlook["oauth_provider"] == "microsoft"
    gmail = next(p for p in resp.json()["providers"] if p["id"] == "gmail")
    assert gmail["supports_oauth"] is False  # Gmail OAuth not in v1


def test_provider_list_outlook_disabled_when_unconfigured(env, monkeypatch):
    monkeypatch.delenv("LUMOGIS_OAUTH_MICROSOFT_CLIENT_ID", raising=False)
    with _client() as client:
        resp = client.get("/api/v1/me/email-providers")
    outlook = next(p for p in resp.json()["providers"] if p["id"] == "outlook")
    assert outlook["enabled"] is False and outlook["supports_oauth"] is False
    assert outlook["disabled_reason"]  # honest disabled-with-reason (no dead-end)
