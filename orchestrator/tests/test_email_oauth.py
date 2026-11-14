# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for services/email_oauth.py (LUM-686 / 674a).

Provider-agnostic token refresh + XOAUTH2 helpers, proven against a fake httpx
transport and a fake ``tethered`` module — no real OAuth app or provider.
"""

from __future__ import annotations

import base64
import sys

import httpx
import pytest
from connectors.registry import IMAP

from services import connector_credentials as ccs
from services import email_oauth as eo

_XOAUTH2 = {
    "host": "imap.example.com",
    "port": 993,
    "username": "alice@example.com",
    "use_ssl": True,
    "auth_type": "xoauth2",
    "provider": "google",
    "refresh_token": "rt-abc",
}


@pytest.fixture(autouse=True)
def _client_id(monkeypatch):
    # A configured BYO client id for google (installation-wide env).
    monkeypatch.setenv("LUMOGIS_OAUTH_GOOGLE_CLIENT_ID", "cid-123")


def _mock_httpx(monkeypatch, handler):
    """Route every ``httpx.Client(...)`` through a MockTransport running ``handler``
    (real httpx request/response codepath, no network)."""
    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)


def _spy_put(monkeypatch):
    writes: list[dict] = []

    # Real signature: put_payload(user_id, connector, payload, *, actor, instance="").
    def fake_put(user_id, connector, payload, *, actor, instance=""):
        writes.append(
            {
                "user_id": user_id,
                "connector": connector,
                "payload": payload,
                "actor": actor,
                "instance": instance,
            }
        )

    monkeypatch.setattr(ccs, "put_payload", fake_put)
    return writes


def test_cached_token_returned_when_unexpired(monkeypatch):
    calls = {"n": 0}

    def handler(request):  # pragma: no cover - must NOT be called
        calls["n"] += 1
        return httpx.Response(200, json={})

    _mock_httpx(monkeypatch, handler)
    writes = _spy_put(monkeypatch)
    payload = dict(_XOAUTH2, access_token="CACHED", expiry=eo.time.time() + 3600)
    tok = eo.get_access_token("u1", connector=IMAP, instance="src-1", payload=payload)
    assert tok == "CACHED"
    assert calls["n"] == 0  # no HTTP
    assert writes == []  # nothing persisted


def test_refreshes_when_expired_and_persists(monkeypatch):
    def handler(request):
        assert str(request.url) == eo._PROVIDER_TOKEN["google"]
        assert b"grant_type=refresh_token" in request.content
        assert b"client_id=cid-123" in request.content
        return httpx.Response(200, json={"access_token": "NEW", "expires_in": 1800})

    _mock_httpx(monkeypatch, handler)
    writes = _spy_put(monkeypatch)
    payload = dict(_XOAUTH2, access_token="OLD", expiry=eo.time.time() - 10)
    tok = eo.get_access_token("u1", connector=IMAP, instance="src-1", payload=payload)
    assert tok == "NEW"
    assert len(writes) == 1
    w = writes[0]
    assert w["actor"] == "system"
    assert w["instance"] == "src-1"
    assert w["payload"]["access_token"] == "NEW"
    assert w["payload"]["expiry"] > eo.time.time()
    # The caller's dict is not mutated (a copy was written).
    assert payload["access_token"] == "OLD"


def test_rotates_refresh_token_when_provider_returns_one(monkeypatch):
    def handler(request):
        return httpx.Response(
            200, json={"access_token": "NEW", "expires_in": 3600, "refresh_token": "rt-rotated"}
        )

    _mock_httpx(monkeypatch, handler)
    writes = _spy_put(monkeypatch)
    eo.get_access_token("u1", connector=IMAP, instance="src-1", payload=dict(_XOAUTH2))
    assert writes[0]["payload"]["refresh_token"] == "rt-rotated"


def test_refresh_non_2xx_raises_and_persists_nothing(monkeypatch):
    def handler(request):
        return httpx.Response(400, json={"error": "invalid_grant", "secret_detail": "DO-NOT-LEAK"})

    _mock_httpx(monkeypatch, handler)
    writes = _spy_put(monkeypatch)
    with pytest.raises(eo.OAuthRefreshError) as ei:
        eo.get_access_token("u1", connector=IMAP, instance="src-1", payload=dict(_XOAUTH2))
    assert writes == []
    # Message hygiene: status + provider only, never the response body.
    msg = str(ei.value)
    assert "400" in msg and "google" in msg
    assert "DO-NOT-LEAK" not in msg and "invalid_grant" not in msg


def test_network_error_raises_oauth_refresh_error(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("boom")

    _mock_httpx(monkeypatch, handler)
    writes = _spy_put(monkeypatch)
    with pytest.raises(eo.OAuthRefreshError):
        eo.get_access_token("u1", connector=IMAP, instance="src-1", payload=dict(_XOAUTH2))
    assert writes == []


def test_unknown_provider_raises(monkeypatch):
    writes = _spy_put(monkeypatch)
    bad = dict(_XOAUTH2, provider="evilcorp")
    with pytest.raises(eo.OAuthRefreshError):
        eo.get_access_token("u1", connector=IMAP, instance="src-1", payload=bad)
    assert writes == []


def test_missing_refresh_token_raises(monkeypatch):
    bad = {k: v for k, v in _XOAUTH2.items() if k != "refresh_token"}
    with pytest.raises(eo.OAuthRefreshError):
        eo.get_access_token("u1", connector=IMAP, instance="src-1", payload=bad)


def test_missing_client_id_raises(monkeypatch):
    monkeypatch.delenv("LUMOGIS_OAUTH_GOOGLE_CLIENT_ID", raising=False)
    payload = dict(_XOAUTH2, expiry=eo.time.time() - 10)
    with pytest.raises(eo.OAuthRefreshError):
        eo.get_access_token("u1", connector=IMAP, instance="src-1", payload=payload)


def test_oauth_refresh_error_is_credential_unavailable():
    assert issubclass(eo.OAuthRefreshError, ccs.CredentialUnavailable)


def test_build_xoauth2_auth_string_matches_sasl_spec():
    s = eo.build_xoauth2_auth_string("alice@example.com", "TOK")
    assert base64.b64decode(s) == b"user=alice@example.com\x01auth=Bearer TOK\x01\x01"


def test_declared_oauth_hosts_only_configured_providers(monkeypatch):
    monkeypatch.setenv("LUMOGIS_OAUTH_GOOGLE_CLIENT_ID", "cid")
    monkeypatch.delenv("LUMOGIS_OAUTH_MICROSOFT_CLIENT_ID", raising=False)
    hosts = eo.declared_oauth_hosts()
    assert "oauth2.googleapis.com" in hosts
    assert "login.microsoftonline.com" not in hosts


def test_declared_oauth_hosts_empty_when_no_client_id(monkeypatch):
    monkeypatch.delenv("LUMOGIS_OAUTH_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("LUMOGIS_OAUTH_MICROSOFT_CLIENT_ID", raising=False)
    assert eo.declared_oauth_hosts() == []


# --- egress scope: refresh runs inside oauth_egress_scope(token_host) ---------


class _FakeTethered:
    def __init__(self):
        self.scopes: list[list[str]] = []

    def scope(self, *, allow, allow_localhost, label):
        self.scopes.append(list(allow))
        import contextlib

        return contextlib.nullcontext()


def test_refresh_runs_inside_egress_scope(monkeypatch):
    monkeypatch.setenv("LUMOGIS_FF_IMAP_EGRESS_GUARD", "true")
    fake = _FakeTethered()
    monkeypatch.setitem(sys.modules, "tethered", fake)

    def handler(request):
        return httpx.Response(200, json={"access_token": "NEW", "expires_in": 3600})

    _mock_httpx(monkeypatch, handler)
    _spy_put(monkeypatch)
    eo.get_access_token("u1", connector=IMAP, instance="src-1", payload=dict(_XOAUTH2))
    # The token host is the scoped allow.
    assert fake.scopes and fake.scopes[0] == ["oauth2.googleapis.com"]


def test_refresh_works_with_egress_flag_off(monkeypatch):
    monkeypatch.setenv("LUMOGIS_FF_IMAP_EGRESS_GUARD", "false")

    def handler(request):
        return httpx.Response(200, json={"access_token": "NEW", "expires_in": 3600})

    _mock_httpx(monkeypatch, handler)
    _spy_put(monkeypatch)
    # Fail-safe-to-functional: no scope, refresh still works.
    tok = eo.get_access_token("u1", connector=IMAP, instance="src-1", payload=dict(_XOAUTH2))
    assert tok == "NEW"


def test_concurrent_refresh_posts_once(monkeypatch):
    """Two threads refreshing the same expired mailbox must serialize to one POST.

    Without the per-mailbox lock, both threads would POST the same refresh_token:
    duplicate POSTs and a lost rotation write (two persisted rotations, last
    writer wins)."""
    import threading

    barrier = threading.Barrier(2)
    http_calls = {"n": 0}
    store = {"payload": dict(_XOAUTH2, access_token="OLD", expiry=eo.time.time() - 10)}

    def handler(request):
        http_calls["n"] += 1
        return httpx.Response(
            200,
            json={"access_token": "NEW", "expires_in": 3600, "refresh_token": "rt-new"},
        )

    _mock_httpx(monkeypatch, handler)

    def fake_get(user_id, connector, instance=""):
        return dict(store["payload"])

    def fake_put(user_id, connector, payload, *, actor, instance=""):
        store["payload"] = payload

    monkeypatch.setattr(ccs, "get_payload", fake_get)
    monkeypatch.setattr(ccs, "put_payload", fake_put)

    stale = dict(store["payload"])
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=5)
            eo.get_access_token("u1", connector=IMAP, instance="src-1", payload=dict(stale))
        except BaseException as exc:  # noqa: BLE001 - collect for assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not errors
    assert http_calls["n"] == 1
    assert store["payload"]["access_token"] == "NEW"
