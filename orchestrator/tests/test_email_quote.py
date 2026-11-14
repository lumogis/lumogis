# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``build_quote`` — owner-scoped, best-effort, never-raises (LUM-698).

The quoted-original block a composer shows (visible + editable) and sends
verbatim. Verifies the two idiomatic styles, the ownership gate reads NO body
for a foreign signal, the bounded locate fetch body source, the nullable
``published_at`` degrade, truncation, and the never-raises contract.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

import pytest
from actions.errors import PermanentActionError

import config
from services import email_quote

_UID = "user-A"
_SID = "11111111-1111-1111-1111-111111111111"
_IMAP_URL = f"imap://{_SID}/100/42"
_WHEN = datetime(2026, 4, 20, 9, 30, tzinfo=timezone.utc)
_DEFAULT_BODY = "Fees are due Friday.\n\nThanks,\nSchool"


class _Store:
    def __init__(self, **row):
        base = {
            "title": "Term 2 fees",
            "from_addr": "office@school.edu",
            "from_display": "Fairview Office",
            "published_at": _WHEN,
        }
        base.update(row)
        self.row = base

    def fetch_one(self, q, params=None):
        ql = (q or "").lower()
        if "select url from signals" in ql:
            return {"url": _IMAP_URL}
        if "from_addr, from_display, published_at" in ql:
            return dict(self.row)
        if "select title from signals" in ql:
            return {"title": self.row.get("title", "")}
        return None


class _BodyFetchSpy:
    """Records whether a body was ever fetched over IMAP."""

    def __init__(self, text: str = "fetched-over-imap"):
        self.called = False
        self.kwargs: dict = {}
        self._text = text

    def __call__(self, source, uid, **kw):
        self.called = True
        self.kwargs = dict(kw)
        return self._text


@pytest.fixture
def owned(monkeypatch):
    monkeypatch.setattr(
        "actions.handlers.imap_writeback._load_owned_source", lambda sid, uid: object()
    )
    monkeypatch.setattr(
        email_quote,
        "fetch_body_text",
        lambda source, uid, **kw: _DEFAULT_BODY,
    )


def _use_store(monkeypatch, store):
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)


# --------------------------------------------------------------------------- #
# styles
# --------------------------------------------------------------------------- #
def test_reply_style_prefixes_every_line(monkeypatch, owned):
    _use_store(monkeypatch, _Store())
    out = email_quote.build_quote("sig-1", user_id=_UID, style="reply")

    assert out.startswith("On ")
    assert "Fairview Office <office@school.edu> wrote:" in out
    body_lines = out.split("\n")[1:]
    assert "> Fees are due Friday." in body_lines
    assert ">" in body_lines  # a blank line becomes a BARE ">" (no trailing space)
    assert "> " not in [ln for ln in body_lines if ln == "> "]  # no "> " placeholders


def test_forward_style_emits_header_block_unquoted(monkeypatch, owned):
    _use_store(monkeypatch, _Store())
    out = email_quote.build_quote("sig-1", user_id=_UID, style="forward")

    assert out.startswith("---------- Forwarded message ----------")
    assert "From: Fairview Office <office@school.edu>" in out
    assert "Subject: Term 2 fees" in out
    # Forward bodies are NOT ">"-prefixed.
    assert "Fees are due Friday." in out
    assert "> Fees are due Friday." not in out


def test_bare_address_when_no_display_name(monkeypatch, owned):
    _use_store(monkeypatch, _Store(from_display=""))
    out = email_quote.build_quote("sig-1", user_id=_UID)
    assert "office@school.edu wrote:" in out
    assert "<office@school.edu>" not in out


# --------------------------------------------------------------------------- #
# ownership — the security-relevant one
# --------------------------------------------------------------------------- #
def test_non_owned_signal_returns_blank_and_reads_no_body(monkeypatch):
    spy = _BodyFetchSpy()
    _use_store(monkeypatch, _Store())
    monkeypatch.setattr(email_quote, "fetch_body_text", spy)

    def _deny(sid, uid):
        raise PermanentActionError("not_found", "not yours")

    monkeypatch.setattr("actions.handlers.imap_writeback._load_owned_source", _deny)

    assert email_quote.build_quote("sig-1", user_id="someone-else") == ""
    # The ownership gate runs BEFORE any body read.
    assert spy.called is False


# --------------------------------------------------------------------------- #
# body source — bounded locate fetch (not INBOX-only)
# --------------------------------------------------------------------------- #
def test_fetch_body_passes_locate_args_to_bounded_fetch(monkeypatch, owned):
    spy = _BodyFetchSpy()
    _use_store(monkeypatch, _Store())
    monkeypatch.setattr(email_quote, "fetch_body_text", spy)

    email_quote.build_quote("sig-1", user_id=_UID)

    assert spy.called is True
    assert spy.kwargs["user_id"] == _UID
    assert spy.kwargs["signal_id"] == "sig-1"
    assert spy.kwargs["uidvalidity"] == 100


def test_imap_fallback_when_bounded_fetch_returns_text(monkeypatch, owned):
    _use_store(monkeypatch, _Store())
    monkeypatch.setattr(
        email_quote,
        "fetch_body_text",
        lambda source, uid, **kw: "fetched-over-imap",
    )
    out = email_quote.build_quote("sig-1", user_id=_UID)
    assert "> fetched-over-imap" in out


# --------------------------------------------------------------------------- #
# degrades
# --------------------------------------------------------------------------- #
def test_null_published_at_omits_the_date_clause(monkeypatch, owned):
    _use_store(monkeypatch, _Store(published_at=None))
    out = email_quote.build_quote("sig-1", user_id=_UID)
    # No literal "None", no invented date — the clause is simply dropped.
    assert out.startswith("Fairview Office <office@school.edu> wrote:")
    assert "None" not in out.split("\n")[0]


def test_truncates_at_max_chars_with_a_marker(monkeypatch, owned):
    monkeypatch.setattr(
        email_quote,
        "fetch_body_text",
        lambda source, uid, **kw: "x" * 40000,
    )
    _use_store(monkeypatch, _Store())
    out = email_quote.build_quote("sig-1", user_id=_UID)
    assert len(out) <= email_quote._MAX_QUOTE_CHARS + len(email_quote._TRUNCATION_MARKER) + 8
    assert out.endswith(email_quote._TRUNCATION_MARKER)


def test_crlf_endings_normalized_before_prefixing(monkeypatch, owned):
    monkeypatch.setattr(
        email_quote,
        "fetch_body_text",
        lambda source, uid, **kw: "line one\r\nline two",
    )
    _use_store(monkeypatch, _Store())
    out = email_quote.build_quote("sig-1", user_id=_UID)
    assert "\r" not in out
    assert "> line one" in out and "> line two" in out


def test_never_raises_on_store_error(monkeypatch, owned):
    class _Boom:
        def fetch_one(self, q, params=None):
            raise RuntimeError("db down")

    _use_store(monkeypatch, _Boom())
    assert email_quote.build_quote("sig-1", user_id=_UID) == ""


def test_blank_when_signal_is_not_an_imap_message(monkeypatch, owned):
    class _NonImap(_Store):
        def fetch_one(self, q, params=None):
            if "select url from signals" in (q or "").lower():
                return {"url": "https://example.com/article"}
            return super().fetch_one(q, params)

    _use_store(monkeypatch, _NonImap())
    assert email_quote.build_quote("sig-1", user_id=_UID) == ""


def test_fetch_body_text_delegates_to_bounded_fetch(monkeypatch):
    seen: dict = {}

    class _Adapter:
        def __init__(self, source):
            pass

        def fetch_body_bounded(self, **kw):
            seen.update(kw)
            from adapters.email_imap_source import FetchedBody

            return FetchedBody("hello", "ok")

    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _Adapter)
    monkeypatch.setattr(
        "services.email_message_locate.message_id_for", lambda s, u: "<mid@example.com>"
    )
    monkeypatch.setattr(
        "services.email_message_locate.organize_folder_for", lambda s, u: "Lumogis/Receipts"
    )

    out = email_quote.fetch_body_text(object(), 7, user_id=_UID, signal_id="sig-1", uidvalidity=100)

    assert out == "hello"
    assert seen["uid"] == 7
    assert seen["message_id"] == "<mid@example.com>"
    assert seen["folder_hint"] == "Lumogis/Receipts"
    assert seen["uidvalidity"] == 100


def test_empty_body_still_emits_the_attribution(monkeypatch, owned):
    _use_store(monkeypatch, _Store())
    monkeypatch.setattr(email_quote, "fetch_body_text", lambda source, uid, **kw: "")
    out = email_quote.build_quote("sig-1", user_id=_UID)
    assert out.endswith("wrote:")
