# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for the read-only mailbox probe (LUM-644 pre-save live test).

Uses a fake IMAPClient with **bytes** keys (imapclient's real shape) so a probe
pinned on str keys would fail here, mirroring email_imap_source.py:117.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from services import email_credentials
from services import imap_transport as it


class _FakeProbeClient:
    def __init__(self, *, raise_status=False):
        self._raise_status = raise_status
        self.logged_out = False
        self.write_calls: list = []

    def list_folders(self):
        # (flags, delimiter, name) — SPECIAL-USE flags as bytes.
        return [
            ([b"\\HasNoChildren"], b"/", "INBOX"),
            ([b"\\Sent"], b"/", "Sent"),
            ([b"\\Trash"], b"/", "Trash"),
            ([b"\\HasNoChildren"], b"/", "Receipts"),
            ([], b"/", "[Gmail]/All Mail"),
        ]

    def folder_status(self, folder, keys):
        if self._raise_status:
            raise RuntimeError("no INBOX")
        return {b"MESSAGES": 42, b"UIDVALIDITY": 100}  # bytes keys

    def capabilities(self):
        return [b"IMAP4REV1", b"IDLE"]

    # Any of these firing would mean the probe mutated the mailbox.
    def create_folder(self, *a, **k):
        self.write_calls.append(("create_folder", a))

    def delete_messages(self, *a, **k):
        self.write_calls.append(("delete_messages", a))

    def logout(self):
        self.logged_out = True


def _conn():
    return email_credentials.ImapConnection(
        host="imap.example.com",
        port=993,
        username="a@b.com",
        use_ssl=True,
        auth_type="app_password",
        password="pw",
    )


def test_probe_client_maps_roles_counts_and_caps():
    client = _FakeProbeClient()
    result = it._probe_client(client)
    assert result.ok is True
    assert result.message_count == 42  # bytes key read
    assert "IMAP4REV1" in result.capabilities
    by_name = {f["name"]: f for f in result.folders}
    assert by_name["Sent"]["role"] == "sent" and by_name["Sent"]["hidden"] is True
    assert by_name["Trash"]["hidden"] is True
    assert by_name["Receipts"]["hidden"] is False and by_name["Receipts"]["selected"] is True
    assert by_name["[Gmail]/All Mail"]["hidden"] is True  # hidden by name
    assert client.write_calls == []  # read-only


def test_probe_partial_when_folder_status_fails():
    client = _FakeProbeClient(raise_status=True)
    result = it._probe_client(client)
    assert result.ok is True  # auth worked
    assert result.message_count is None  # degraded, not a failed test
    assert any(f["name"] == "Receipts" for f in result.folders)


def test_probe_short_circuits_on_proxy(monkeypatch):
    monkeypatch.setattr(it, "_proxy_configured_for", lambda conn: True)
    # connect must NOT be called on a proxied deployment.
    monkeypatch.setattr(it, "connect", lambda *a, **k: pytest.fail("connect called"))
    result = it.probe(_conn(), user_id="u1")
    assert result.ok is False and result.proxy_unverified is True


def test_probe_runs_inside_egress_scope_and_logs_out(monkeypatch):
    scoped = {"entered": False}

    @contextmanager
    def fake_scope(conn, *, user_id=None):
        scoped["entered"] = True
        yield

    client = _FakeProbeClient()
    monkeypatch.setattr(it, "_proxy_configured_for", lambda conn: False)
    monkeypatch.setattr(it.egress_guard, "imap_egress_scope", fake_scope)
    monkeypatch.setattr(it, "connect", lambda conn, timeout=None: client)
    result = it.probe(_conn(), user_id="u1", timeout=5.0)
    assert result.ok is True and result.message_count == 42
    assert scoped["entered"] is True  # egress-scoped
    assert client.logged_out is True
