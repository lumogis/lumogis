# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for adapters/email_imap_source.py (LUM-170).

Uses a fake IMAPClient (injected via ``client_factory``) so the tests run
without the ``imapclient`` dependency or a live server. Pins: metadata-only
Signal shape, PII-free UIDVALIDITY-safe url, ``*``-range client-side filter,
per-tick cap, cursor advance, and the UIDVALIDITY-reset path.
"""

from __future__ import annotations

import pytest
from adapters import email_imap_source as eis
from adapters.email_imap_source import EmailImapSource
from models.signals import SourceConfig

from services import email_credentials


def _source(**kw) -> SourceConfig:
    base = dict(
        id="src-abc",
        name="Personal mail",
        source_type="imap",
        url="imap://imap.example.com:993/personal",
        category="",
        active=True,
        poll_interval=900,
        extraction_method="imap_metadata",
        css_selector_override=None,
        last_polled_at=None,
        last_signal_at=None,
        poll_cursor=None,
        user_id="u1",
    )
    base.update(kw)
    return SourceConfig(**base)


def _hdr(subject: str, sender: str = "a@b.com", list_unsub: str | None = None) -> bytes:
    lines = [
        f"From: {sender}",
        f"Subject: {subject}",
        "Date: Mon, 1 Jan 2026 00:00:00 +0000",
        "Message-ID: <x@b.com>",
    ]
    if list_unsub:
        lines.append(f"List-Unsubscribe: {list_unsub}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


class _FakeClient:
    def __init__(self, uidvalidity: int, messages: dict[int, bytes]) -> None:
        self._uidvalidity = uidvalidity
        self._messages = messages  # uid -> header bytes
        self.logged_out = False

    def select_folder(self, folder, readonly=False):
        assert readonly is True
        return {b"UIDVALIDITY": self._uidvalidity}

    def search(self, criteria):
        # criteria == ["UID", "{low}:*"] — model the RFC 3501 `*` semantics:
        # when nothing is >= low, the range collapses to the max UID.
        low = int(criteria[1].split(":", 1)[0])
        uids = sorted(self._messages)
        matches = [u for u in uids if u >= low]
        if not matches and uids:
            return [uids[-1]]  # `*` collapse to highest
        return matches

    def fetch(self, uids, keys):
        out = {}
        for u in uids:
            out[u] = {eis._HEADER_RESP_KEY: self._messages[u]}
        return out

    def logout(self):
        self.logged_out = True


@pytest.fixture
def conn(monkeypatch):
    c = email_credentials.ImapConnection(
        host="imap.example.com",
        port=993,
        username="alice@example.com",
        use_ssl=True,
        auth_type="app_password",
        password="app-pw",
    )
    monkeypatch.setattr(email_credentials, "load_connection", lambda user_id, *, instance: c)
    return c


def test_poll_builds_metadata_signals(conn):
    client = _FakeClient(100, {5: _hdr("Hello"), 6: _hdr("World")})
    src = EmailImapSource(_source(), client_factory=lambda _c: client)
    signals = src.poll(since=None)

    assert len(signals) == 2
    for s in signals:
        assert s.source_type == "imap"
        assert s.raw_content == ""
        assert s.headers.get("Subject") in ("Hello", "World")
        # PII-free, UIDVALIDITY-safe url — no username embedded.
        assert s.url.startswith("imap://src-abc/100/")
        assert "@" not in s.url
    assert src.result_cursor == "100:6"
    assert client.logged_out is True


def test_poll_incremental_filters_star_collapse(conn):
    # cursor at 100:6; server has nothing new but `{7}:*` returns uid 6 (collapse).
    client = _FakeClient(100, {6: _hdr("Old")})
    src = EmailImapSource(_source(), client_factory=lambda _c: client)
    signals = src.poll(since="100:6")
    assert signals == []
    assert src.result_cursor == "100:6"


def test_poll_uidvalidity_reset_reseeds(conn):
    # Stored cursor is for uidvalidity 100; server now reports 200 → reset.
    client = _FakeClient(200, {1: _hdr("New after reset")})
    src = EmailImapSource(_source(), client_factory=lambda _c: client)
    signals = src.poll(since="100:6")
    assert len(signals) == 1
    assert signals[0].url == "imap://src-abc/200/1"
    assert src.result_cursor == "200:1"


def test_poll_caps_at_max_msgs_per_tick(conn, monkeypatch):
    monkeypatch.setattr(eis, "_MAX_EMAIL_MSGS_PER_TICK", 3)
    client = _FakeClient(100, {i: _hdr(f"m{i}") for i in range(1, 11)})
    src = EmailImapSource(_source(), client_factory=lambda _c: client)
    signals = src.poll(since=None)
    assert len(signals) == 3  # capped
    # cursor advanced only past the batch actually processed
    assert src.result_cursor == "100:3"
