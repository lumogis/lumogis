# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for the Device Authorization Grant state machine (LUM-687 / 674b).

Fake device-auth + token endpoints via httpx.MockTransport; no real provider.
"""

from __future__ import annotations

import sys

import httpx
import pytest

from services import email_oauth as eo

# Capture the real client ONCE so re-patching within a test swaps only the handler
# (a factory that re-reads httpx.Client would nest and overwrite the transport).
_REAL_HTTPX_CLIENT = httpx.Client


@pytest.fixture(autouse=True)
def _client_id(monkeypatch):
    monkeypatch.setenv("LUMOGIS_OAUTH_MICROSOFT_CLIENT_ID", "cid-ms")
    # Isolate the in-process flow store between tests.
    eo._FLOWS.clear()


def _mock_httpx(monkeypatch, handler):
    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _REAL_HTTPX_CLIENT(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)


_START_KW = dict(
    provider="microsoft",
    email="a@b.com",
    mailbox_label="work",
    host="outlook.office365.com",
    port=993,
    smtp_host="smtp.office365.com",
    smtp_port=587,
)


def _device_ok(request):
    return httpx.Response(
        200,
        json={
            "device_code": "DEV-SECRET",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://microsoft.com/devicelogin",
            "verification_uri_complete": "https://microsoft.com/devicelogin?otc=ABCD-EFGH",
            "interval": 5,
            "expires_in": 900,
        },
    )


def test_start_returns_browser_safe_fields_device_code_stays_server(monkeypatch):
    _mock_httpx(monkeypatch, _device_ok)
    flow = eo.start_device_flow("u1", **_START_KW)
    assert flow.user_code == "ABCD-EFGH"
    assert flow.verification_uri.startswith("https://")
    assert flow.interval == 5 and flow.expires_in == 900
    # The device_code is in the store, never on the DeviceFlow.
    assert not hasattr(flow, "device_code")
    assert eo._FLOWS[flow.flow_id]["device_code"] == "DEV-SECRET"
    assert eo._FLOWS[flow.flow_id]["user_id"] == "u1"


def test_start_defaults_import_scope_and_get_flow_exposes_it(monkeypatch):
    _mock_httpx(monkeypatch, _device_ok)
    flow = eo.start_device_flow("u1", **_START_KW)  # no import_scope passed
    assert eo._FLOWS[flow.flow_id]["import_scope"] == "last_30d"
    got = eo.get_flow("u1", flow.flow_id)
    assert got["import_scope"] == "last_30d"
    assert got["import_since"] is None and got["import_before"] is None


def test_start_carries_custom_import_scope_through_get_flow(monkeypatch):
    import datetime

    _mock_httpx(monkeypatch, _device_ok)
    since = datetime.date(2026, 1, 1)
    flow = eo.start_device_flow(
        "u1",
        **_START_KW,
        import_scope="custom",
        import_since=since,
        import_before=None,
    )
    got = eo.get_flow("u1", flow.flow_id)
    assert got["import_scope"] == "custom" and got["import_since"] == since


def test_start_parses_google_verification_url_spelling(monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "device_code": "D",
                "user_code": "U",
                "verification_url": "https://g.co/dev",  # Google spelling
                "interval": 5,
                "expires_in": 900,
            },
        )

    _mock_httpx(monkeypatch, handler)
    flow = eo.start_device_flow("u1", **_START_KW)
    assert flow.verification_uri == "https://g.co/dev"


def test_start_no_client_id_raises(monkeypatch):
    monkeypatch.delenv("LUMOGIS_OAUTH_MICROSOFT_CLIENT_ID", raising=False)
    with pytest.raises(eo.OAuthDeviceError):
        eo.start_device_flow("u1", **_START_KW)


def test_start_unknown_provider_raises(monkeypatch):
    _mock_httpx(monkeypatch, _device_ok)
    with pytest.raises(eo.OAuthDeviceError):
        eo.start_device_flow("u1", **{**_START_KW, "provider": "google"})


def _start(monkeypatch):
    _mock_httpx(monkeypatch, _device_ok)
    return eo.start_device_flow("u1", **_START_KW)


def test_poll_pending_then_authorized(monkeypatch):
    flow = _start(monkeypatch)

    # First poll: force the cadence gate open, then pending.
    eo._FLOWS[flow.flow_id]["next_poll_at"] = 0
    _mock_httpx(monkeypatch, lambda r: httpx.Response(400, json={"error": "authorization_pending"}))
    assert eo.poll_device_flow("u1", flow.flow_id).status == "pending"

    eo._FLOWS[flow.flow_id]["next_poll_at"] = 0
    _mock_httpx(
        monkeypatch,
        lambda r: httpx.Response(
            200, json={"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}
        ),
    )
    poll = eo.poll_device_flow("u1", flow.flow_id)
    assert poll.status == "authorized"
    assert poll.refresh_token == "RT" and poll.access_token == "AT"
    # Flow kept (in_progress) so a concurrent poll sees pending, until discard.
    assert eo._FLOWS[flow.flow_id]["in_progress"] is True
    assert eo.poll_device_flow("u1", flow.flow_id).status == "pending"
    eo.discard_flow(flow.flow_id)
    assert eo.poll_device_flow("u1", flow.flow_id).status == "not_found"


def test_poll_honours_interval(monkeypatch):
    flow = _start(monkeypatch)
    # next_poll_at is in the future → pending without hitting the provider.
    called = {"n": 0}
    _mock_httpx(
        monkeypatch,
        lambda r: (called.__setitem__("n", called["n"] + 1), httpx.Response(200, json={}))[1],
    )
    assert eo.poll_device_flow("u1", flow.flow_id).status == "pending"
    assert called["n"] == 0


def test_poll_slow_down_widens_interval(monkeypatch):
    flow = _start(monkeypatch)
    before = eo._FLOWS[flow.flow_id]["interval"]
    eo._FLOWS[flow.flow_id]["next_poll_at"] = 0
    _mock_httpx(monkeypatch, lambda r: httpx.Response(400, json={"error": "slow_down"}))
    assert eo.poll_device_flow("u1", flow.flow_id).status == "pending"
    assert eo._FLOWS[flow.flow_id]["interval"] == before + eo._SLOW_DOWN_INCREMENT_SECONDS


def test_poll_expired_and_denied(monkeypatch):
    for err, expect in (
        ("expired_token", "expired"),
        ("access_denied", "denied"),
        ("authorization_declined", "denied"),
        ("bad_verification_code", "denied"),
    ):
        flow = _start(monkeypatch)
        eo._FLOWS[flow.flow_id]["next_poll_at"] = 0
        _mock_httpx(monkeypatch, lambda r, e=err: httpx.Response(400, json={"error": e}))
        assert eo.poll_device_flow("u1", flow.flow_id).status == expect
        assert flow.flow_id not in eo._FLOWS  # dropped


def test_poll_unknown_error_drops_and_raises(monkeypatch):
    flow = _start(monkeypatch)
    eo._FLOWS[flow.flow_id]["next_poll_at"] = 0
    _mock_httpx(monkeypatch, lambda r: httpx.Response(400, json={"error": "weird_thing"}))
    with pytest.raises(eo.OAuthDeviceError):
        eo.poll_device_flow("u1", flow.flow_id)
    assert flow.flow_id not in eo._FLOWS


def test_poll_success_without_refresh_token_raises(monkeypatch):
    flow = _start(monkeypatch)
    eo._FLOWS[flow.flow_id]["next_poll_at"] = 0
    _mock_httpx(monkeypatch, lambda r: httpx.Response(200, json={"access_token": "AT"}))
    with pytest.raises(eo.OAuthDeviceError):
        eo.poll_device_flow("u1", flow.flow_id)


def test_poll_owner_scoped(monkeypatch):
    flow = _start(monkeypatch)
    assert eo.poll_device_flow("intruder", flow.flow_id).status == "not_found"


def test_poll_network_error_releases_and_raises(monkeypatch):
    flow = _start(monkeypatch)
    eo._FLOWS[flow.flow_id]["next_poll_at"] = 0

    def boom(r):
        raise httpx.ConnectError("down")

    _mock_httpx(monkeypatch, boom)
    with pytest.raises(eo.OAuthDeviceError):
        eo.poll_device_flow("u1", flow.flow_id)
    # Flow still present, in_progress cleared → retryable.
    assert eo._FLOWS[flow.flow_id]["in_progress"] is False


def test_get_flow_never_exposes_device_code(monkeypatch):
    flow = _start(monkeypatch)
    got = eo.get_flow("u1", flow.flow_id)
    assert got is not None and "device_code" not in got
    assert got["email"] == "a@b.com" and got["provider"] == "microsoft"
    assert eo.get_flow("intruder", flow.flow_id) is None


def test_device_post_runs_inside_egress_scope(monkeypatch):
    monkeypatch.setenv("LUMOGIS_FF_IMAP_EGRESS_GUARD", "true")

    class _FakeTethered:
        def __init__(self):
            self.scopes = []

        def scope(self, *, allow, allow_localhost, label):
            import contextlib

            self.scopes.append(list(allow))
            return contextlib.nullcontext()

    fake = _FakeTethered()
    monkeypatch.setitem(sys.modules, "tethered", fake)
    _mock_httpx(monkeypatch, _device_ok)
    eo.start_device_flow("u1", **_START_KW)
    assert fake.scopes and fake.scopes[0] == ["login.microsoftonline.com"]


def test_has_oauth_provider_gating(monkeypatch):
    assert eo.has_oauth_provider("microsoft") is True
    assert eo.has_oauth_provider("gmail") is False  # not a device provider
    monkeypatch.delenv("LUMOGIS_OAUTH_MICROSOFT_CLIENT_ID", raising=False)
    assert eo.has_oauth_provider("microsoft") is False  # no client_id
