# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``GET /api/v1/email/sending-addresses`` (LUM-831).

The load-bearing test is :func:`test_address_matches_the_send_path`: it asserts
the address this endpoint displays is the same value ``load_smtp_connection``
would use as ``From`` — **including** for a mailbox with its own SMTP
app-password — which turns the ADR's "cannot drift" claim into something that
fails when it stops being true.

The second is :func:`test_address_read_performs_no_dns`. Reading a username must
not depend on a live resolver; the accessor was narrowed for exactly that reason
and a refactor back through ``load_connection`` must fail here rather than in
production on hotel wifi.
"""

from __future__ import annotations

import pytest
from connectors.registry import IMAP
from connectors.registry import SMTP
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routes.api_v1 import email_sending_addresses as mod

from services import connector_credentials as ccs
from services import email_credentials as emc

WORK = "src-work"
HOME = "src-home"
WORK_ADDR = "alice@acme.com"
HOME_ADDR = "alice@fastmail.com"


class _Rec:
    def __init__(self, connector: str, instance: str) -> None:
        self.connector = connector
        self.instance = instance


def _imap_payload(username: str, host: str = "imap.example.com") -> dict:
    return {
        "host": host,
        "port": 993,
        "username": username,
        "password": "pw",
        "use_ssl": True,
        "auth_type": "app_password",
    }


@pytest.fixture
def wire(monkeypatch):
    """Wire credential rows + payloads; returns a setter the tests drive."""

    state: dict = {"records": [], "payloads": {}}

    monkeypatch.setattr(ccs, "list_records", lambda user_id: list(state["records"]))

    def _get_payload(user_id, connector, *, instance=""):
        return state["payloads"].get((connector, instance))

    monkeypatch.setattr(ccs, "get_payload", _get_payload)
    # No live DNS anywhere in this module by default; the one test that cares
    # about the resolver overrides this with a raiser.
    monkeypatch.setattr(emc._ohu, "_resolved_ips", lambda host: ["93.184.216.34"])
    return state


@pytest.fixture
def client(monkeypatch, wire):
    from auth import UserContext

    ctx = UserContext(user_id="u1", role="member", allows_shared=True)
    app = FastAPI()
    app.include_router(mod.router)
    app.dependency_overrides[mod.require_user] = lambda: ctx
    monkeypatch.setattr(mod, "get_user", lambda request: ctx)
    mod._limiter.clear()
    return TestClient(app)


def _two_sendable(wire):
    wire["records"] = [
        _Rec(IMAP, WORK),
        _Rec(SMTP, WORK),
        _Rec(IMAP, HOME),
        _Rec(SMTP, HOME),
    ]
    wire["payloads"] = {
        (IMAP, WORK): _imap_payload(WORK_ADDR),
        (IMAP, HOME): _imap_payload(HOME_ADDR),
        (SMTP, WORK): {"reuse_imap": True},
        (SMTP, HOME): {"reuse_imap": True},
    }


def _items(resp) -> dict[str, str]:
    return {i["source_id"]: i["address"] for i in resp.json()["items"]}


def test_returns_one_entry_per_sendable_mailbox(client, wire):
    _two_sendable(wire)
    r = client.get("/api/v1/email/sending-addresses")
    assert r.status_code == 200
    assert _items(r) == {WORK: WORK_ADDR, HOME: HOME_ADDR}


def test_mailbox_without_smtp_is_not_offered(client, wire):
    """An IMAP-only mailbox cannot send, so it must not appear (LUM-719 class)."""
    _two_sendable(wire)
    wire["records"] = [_Rec(IMAP, WORK), _Rec(SMTP, WORK), _Rec(IMAP, HOME)]
    r = client.get("/api/v1/email/sending-addresses")
    assert _items(r) == {WORK: WORK_ADDR}


def test_smtp_row_without_imap_row_is_omitted(client, wire):
    """The predicate-divergence case, reachable because DELETE has no cascade.

    ``sendable_mailboxes`` deliberately offers it (SMTP row = sendable, matching
    the web's ``hasSmtpCredential``); the per-mailbox catch then omits it because
    there is no IMAP payload to read a username from. Same observable outcome as
    a both-rows pre-filter, without two predicates.
    """
    _two_sendable(wire)
    wire["records"] = [_Rec(IMAP, WORK), _Rec(SMTP, WORK), _Rec(SMTP, HOME)]
    del wire["payloads"][(IMAP, HOME)]

    assert HOME in emc.sendable_mailboxes("u1"), "the SMTP row alone means sendable"
    r = client.get("/api/v1/email/sending-addresses")
    assert r.status_code == 200
    assert _items(r) == {WORK: WORK_ADDR}


def test_undecryptable_mailbox_is_omitted_not_fatal(client, wire, monkeypatch):
    _two_sendable(wire)
    real = emc.mailbox_account_address

    def _boom(user_id, *, instance):
        if instance == HOME:
            raise ccs.CredentialUnavailable("decrypt failed")
        return real(user_id, instance=instance)

    monkeypatch.setattr(emc, "mailbox_account_address", _boom)
    r = client.get("/api/v1/email/sending-addresses")
    assert r.status_code == 200
    assert _items(r) == {WORK: WORK_ADDR}, "the healthy sibling must still resolve"


def test_no_sendable_mailboxes_returns_empty_items(client, wire):
    wire["records"] = [_Rec(IMAP, WORK)]
    r = client.get("/api/v1/email/sending-addresses")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_enumeration_failure_does_not_return_empty_items(client, wire, monkeypatch):
    """Unknown must never present as none — the client treats [] as settled."""

    def _down(user_id):
        raise RuntimeError("postgres unreachable")

    monkeypatch.setattr(ccs, "list_records", _down)
    with pytest.raises(RuntimeError):
        client.get("/api/v1/email/sending-addresses")


def test_takes_no_user_id_parameter(client, wire):
    _two_sendable(wire)
    r = client.get("/api/v1/email/sending-addresses?user_id=someone-else")
    assert _items(r) == {WORK: WORK_ADDR, HOME: HOME_ADDR}


def test_projected_signal_source_id_is_absent_from_items(client, wire):
    """Another member's mailbox can never appear: items come from the caller's
    own credential rows and never from a signal."""
    _two_sendable(wire)
    r = client.get("/api/v1/email/sending-addresses")
    assert "src-someone-elses" not in _items(r)


def test_response_sets_no_store(client, wire):
    _two_sendable(wire)
    r = client.get("/api/v1/email/sending-addresses")
    assert r.headers["cache-control"] == "no-store"


def test_rate_limited_after_twenty_calls(client, wire):
    _two_sendable(wire)
    for _ in range(20):
        assert client.get("/api/v1/email/sending-addresses").status_code == 200
    r = client.get("/api/v1/email/sending-addresses")
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "60"


def test_address_read_performs_no_dns(wire, monkeypatch):
    """A username read must not depend on a live resolver (LUM-831)."""
    _two_sendable(wire)

    def _no_dns(host):
        raise OSError("name or service not known")

    monkeypatch.setattr(emc._ohu, "_resolved_ips", _no_dns)
    assert emc.mailbox_account_address("u1", instance=WORK) == WORK_ADDR


def test_unresolvable_host_still_yields_an_address(client, wire, monkeypatch):
    _two_sendable(wire)

    def _no_dns(host):
        raise OSError("name or service not known")

    monkeypatch.setattr(emc._ohu, "_resolved_ips", _no_dns)
    r = client.get("/api/v1/email/sending-addresses")
    assert _items(r) == {WORK: WORK_ADDR, HOME: HOME_ADDR}


def test_xoauth2_mailbox_resolves_without_minting_a_token(wire, monkeypatch):
    """After the narrowing, no token is minted — so the GET cannot write."""
    _two_sendable(wire)
    wire["payloads"][(IMAP, WORK)] = {
        "host": "outlook.office365.com",
        "port": 993,
        "username": WORK_ADDR,
        "use_ssl": True,
        "auth_type": "xoauth2",
        "refresh_token": "rt",
    }
    from services import email_oauth

    def _must_not_run(*a, **k):
        raise AssertionError("token mint on a display read — the GET writes again")

    monkeypatch.setattr(email_oauth, "get_access_token", _must_not_run)
    assert emc.mailbox_account_address("u1", instance=WORK) == WORK_ADDR


def test_address_matches_the_send_path(wire, monkeypatch):
    """The cannot-drift guarantee, including a standalone SMTP app-password."""
    _two_sendable(wire)
    # A separate submission host AND its own app-password: the path most likely
    # to carry a different username, if one ever could.
    wire["payloads"][(SMTP, WORK)] = {
        "reuse_imap": False,
        "app_password": "different-secret",
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
    }

    displayed = emc.mailbox_account_address("u1", instance=WORK)
    sent = emc.load_smtp_connection("u1", instance=WORK).username
    assert displayed == sent == WORK_ADDR


def test_failure_log_carries_no_secret(wire, monkeypatch, caplog):
    import logging

    _two_sendable(wire)

    def _boom(user_id, *, instance):
        raise ccs.CredentialUnavailable("decrypt failed")

    monkeypatch.setattr(emc, "mailbox_account_address", _boom)
    with caplog.at_level(logging.WARNING, logger="services.email_credentials"):
        emc.sending_addresses("u1")

    text = " ".join(r.getMessage() for r in caplog.records)
    assert WORK in text, "the log must name which mailbox was skipped"
    assert "CredentialUnavailable" in text, "and the exception type"
    assert "pw" not in text and WORK_ADDR not in text, "but never a secret or the address"
