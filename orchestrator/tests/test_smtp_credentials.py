# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""SMTP credential resolution (LUM-650): reuse-IMAP default, separate password,
provider host/port derivation, and the "Other"-mailbox host requirement."""

from __future__ import annotations

import pytest
from services.email_credentials import ImapConnection

from services import connector_credentials as ccs
from services import email_credentials as ec

_UID = "user-A"
_SID = "mbox-1"


def _imap(host="imap.gmail.com", username="alice@gmail.com", password="imap-pw"):
    return ImapConnection(
        host=host,
        port=993,
        username=username,
        use_ssl=True,
        auth_type="app_password",
        password=password,
    )


@pytest.fixture
def patch_layers(monkeypatch):
    state = {"smtp_payload": None, "imap": _imap()}

    def _get_payload(uid, connector, *, instance=""):
        if connector == "smtp":
            return state["smtp_payload"]
        return None

    monkeypatch.setattr(ccs, "get_payload", _get_payload)
    monkeypatch.setattr(ec, "load_connection", lambda uid, *, instance: state["imap"])
    return state


def test_reuse_imap_password_default(patch_layers):
    patch_layers["smtp_payload"] = {"reuse_imap": True}
    conn = ec.load_smtp_connection(_UID, instance=_SID)
    # Gmail → registry smtp host/port; username + password reuse the IMAP row.
    assert (conn.host, conn.port) == ("smtp.gmail.com", 587)
    assert conn.username == "alice@gmail.com"
    assert conn.password == "imap-pw"


def test_separate_smtp_password(patch_layers):
    patch_layers["smtp_payload"] = {"reuse_imap": False, "app_password": "smtp-only-pw"}
    conn = ec.load_smtp_connection(_UID, instance=_SID)
    assert conn.password == "smtp-only-pw"
    assert conn.username == "alice@gmail.com"  # From is still the account


def test_separate_password_required_when_not_reusing(patch_layers):
    patch_layers["smtp_payload"] = {"reuse_imap": False}  # no app_password
    with pytest.raises(ValueError):
        ec.load_smtp_connection(_UID, instance=_SID)


def test_other_provider_uses_payload_host(patch_layers):
    patch_layers["imap"] = _imap(host="imap.example.org", username="me@example.org")
    patch_layers["smtp_payload"] = {
        "reuse_imap": True,
        "smtp_host": "mail.example.org",
        "smtp_port": 2525,
    }
    conn = ec.load_smtp_connection(_UID, instance=_SID)
    assert (conn.host, conn.port) == ("mail.example.org", 2525)


def test_other_provider_without_host_is_error(patch_layers):
    patch_layers["imap"] = _imap(host="imap.example.org", username="me@example.org")
    patch_layers["smtp_payload"] = {"reuse_imap": True}  # unknown provider + no host
    with pytest.raises(ValueError):
        ec.load_smtp_connection(_UID, instance=_SID)


def test_missing_smtp_row_not_configured(patch_layers):
    patch_layers["smtp_payload"] = None
    with pytest.raises(ccs.ConnectorNotConfigured):
        ec.load_smtp_connection(_UID, instance=_SID)


def test_resolve_smtp_host_port_known_provider_from_url(monkeypatch):
    monkeypatch.setattr(
        "services.email_credentials._hosts_from_source_url",
        lambda uid, inst: ("imap.fastmail.com", 993),
    )
    imap_decrypt_calls = []
    monkeypatch.setattr(
        ccs,
        "get_payload",
        lambda uid, connector, *, instance="": (
            imap_decrypt_calls.append(connector) or {"reuse_imap": True}
            if connector == "smtp"
            else pytest.fail("IMAP decrypt must not run when url hit")
        ),
    )
    assert ec.resolve_smtp_host_port(_UID, instance=_SID) == ("smtp.fastmail.com", 465)
    assert imap_decrypt_calls == ["smtp"]


def test_resolve_smtp_host_port_other_explicit_smtp_host_still_decrypts(monkeypatch):
    monkeypatch.setattr(
        "services.email_credentials._hosts_from_source_url",
        lambda uid, inst: ("imap.unknown.example", 993),
    )
    monkeypatch.setattr(
        ccs,
        "get_payload",
        lambda uid, connector, *, instance="": (
            {
                "reuse_imap": True,
                "smtp_host": "mail.custom.example",
                "smtp_port": 2525,
            }
            if connector == "smtp"
            else None
        ),
    )
    assert ec.resolve_smtp_host_port(_UID, instance=_SID) == ("mail.custom.example", 2525)


def test_resolve_smtp_host_port_unresolvable_is_none(monkeypatch):
    monkeypatch.setattr(
        "services.email_credentials._hosts_from_source_url",
        lambda uid, inst: ("imap.unknown.example", 993),
    )
    monkeypatch.setattr(
        ccs,
        "get_payload",
        lambda uid, connector, *, instance="": (
            {"reuse_imap": True} if connector == "smtp" else None
        ),
    )
    assert ec.resolve_smtp_host_port(_UID, instance=_SID) is None
