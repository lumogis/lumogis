# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Reply threading — RFC-correct In-Reply-To / References (LUM-651).

Covers the References construction (RFC 5322 §3.6.4 incl. the In-Reply-To
fallback and thread-root case), the folded-header normalization + CRLF-injection
neutralization, the truncation guard, the adapter's read-only thread-header
re-fetch (+ miss), the build_message CR/LF backstop (+ no-regression on clean
values), and the reply-handler wiring (sets both headers, degrades on miss / no
Message-ID).
"""

from __future__ import annotations

import pytest
from actions.errors import PermanentActionError
from actions.handlers import smtp_send
from adapters.email_smtp_sender import SendResult
from adapters.email_smtp_sender import SmtpSender

import config

_UID = "user-A"
_SID = "11111111-1111-1111-1111-111111111111"
_IMAP_URL = f"imap://{_SID}/100/42"


# --------------------------------------------------------------------------- #
# _build_references — RFC 5322 §3.6.4
# --------------------------------------------------------------------------- #
def test_build_references_appends_parent_message_id():
    assert smtp_send._build_references("<a@x> <b@x>", None, "<c@x>") == "<a@x> <b@x> <c@x>"


def test_build_references_normalizes_folded_header():
    # A realistically FOLDED References (parser preserves the folding \r\n ).
    out = smtp_send._build_references("<a@x>\r\n <b@x>", None, "<c@x>")
    assert out == "<a@x> <b@x> <c@x>"
    assert "\r" not in out and "\n" not in out


def test_build_references_fallback_to_in_reply_to():
    # No References on the parent → use its In-Reply-To (single-id chain).
    assert smtp_send._build_references(None, "<a@x>", "<c@x>") == "<a@x> <c@x>"


def test_build_references_thread_root_only():
    assert smtp_send._build_references(None, None, "<c@x>") == "<c@x>"


def test_build_references_empty_when_no_parent_message_id():
    assert smtp_send._build_references("<a@x>", "<b@x>", "") == ""


# --------------------------------------------------------------------------- #
# _truncate_references — pathological-thread cap
# --------------------------------------------------------------------------- #
def test_truncate_references_keeps_root_and_recent():
    ids = " ".join(f"<{i}@x>" for i in range(30))
    out = smtp_send._truncate_references(ids).split()
    assert len(out) == smtp_send._MAX_REFERENCE_IDS
    assert out[0] == "<0@x>"  # thread root preserved
    assert out[-1] == "<29@x>"  # direct parent (last) preserved


def test_truncate_references_under_cap_unchanged():
    ids = " ".join(f"<{i}@x>" for i in range(5))
    assert smtp_send._truncate_references(ids) == ids


def test_truncate_references_byte_cap():
    # Few ids but each huge → exceeds the byte cap → truncated to root + recent.
    ids = " ".join(f"<{'z' * 400}{i}@x>" for i in range(8))
    out = smtp_send._truncate_references(ids)
    assert len(out.split()) == smtp_send._MAX_REFERENCE_IDS or len(out) < len(ids)


# --------------------------------------------------------------------------- #
# CRLF-injection neutralization
# --------------------------------------------------------------------------- #
def test_references_crlf_injection_neutralized():
    # A crafted original References carrying a BARE CRLF + fake header.
    out = smtp_send._build_references("<a@x>\r\nBcc: evil@x.com", None, "<c@x>")
    assert "\r" not in out and "\n" not in out
    # Flattened to inert tokens — never a real header line.
    assert out.startswith("<a@x>")
    assert out.endswith("<c@x>")


def test_build_references_strips_non_whitespace_control_chars():
    # A NUL (or other non-whitespace control) must be stripped, not survive to
    # trip build_message's backstop — the reply must degrade, never dead-letter.
    out = smtp_send._build_references("<a@x>\x00<b@x>", None, "<c@x>")
    assert all(ord(ch) >= 32 or ch == " " for ch in out)
    assert out.endswith("<c@x>")
    # And the built value passes build_message's backstop (no raise).
    msg = SmtpSender.build_message(
        from_addr="a@x.com",
        to=["b@x.com"],
        subject="s",
        body="b",
        references=out,
    )
    assert msg["References"] == out


def test_clean_header_token_strips_control_chars():
    assert "\r" not in smtp_send._clean_header_token("<a@x>\r\nX: y")
    assert "\n" not in smtp_send._clean_header_token("<a@x>\r\nX: y")
    assert smtp_send._clean_header_token(None) == ""
    assert smtp_send._clean_header_token("\r\n\t") == ""


# --------------------------------------------------------------------------- #
# build_message backstop
# --------------------------------------------------------------------------- #
def test_build_message_rejects_crlf_in_threading_headers():
    with pytest.raises(PermanentActionError) as ei:
        SmtpSender.build_message(
            from_addr="a@x.com",
            to=["b@x.com"],
            subject="s",
            body="b",
            in_reply_to="<a>\r\nX: y",
        )
    assert ei.value.code == "header_injection"


def test_build_message_accepts_clean_threading_values():
    # Regression: the new backstop must NOT reject clean threading values.
    msg = SmtpSender.build_message(
        from_addr="a@x.com",
        to=["b@x.com"],
        subject="s",
        body="b",
        in_reply_to="<a@x>",
        references="<a@x> <b@x>",
    )
    assert msg["In-Reply-To"] == "<a@x>"
    assert msg["References"] == "<a@x> <b@x>"


# --------------------------------------------------------------------------- #
# Adapter — fetch_thread_headers (read-only)
# --------------------------------------------------------------------------- #
class _FakeImap:
    def __init__(self, *, uidvalidity=100, fields=None, present=True):
        self.uidvalidity = uidvalidity
        self._fields = fields or {}
        self._present = present
        self.selects: list = []

    def select_folder(self, name, readonly=False):
        self.selects.append((name, readonly))
        return {b"UIDVALIDITY": self.uidvalidity}

    def fetch(self, uids, keys):
        uid = uids[0]
        if not self._present:
            return {uid: {}}
        block = "".join(f"{k}: {v}\r\n" for k, v in self._fields.items()).encode()
        return {uid: {b"BODY[HEADER.FIELDS (SUBJECT MESSAGE-ID REFERENCES IN-REPLY-TO)]": block}}

    def logout(self):
        pass


def _adapter(monkeypatch, fake):
    from models.signals import SourceConfig

    from services import email_credentials

    monkeypatch.setattr(email_credentials, "load_connection", lambda uid, *, instance: object())
    from adapters.email_imap_source import EmailImapSource

    src = SourceConfig(
        id=_SID,
        name="mbox",
        source_type="imap",
        url="imap://x",
        category="",
        active=True,
        poll_interval=3600,
        extraction_method="imap_metadata",
        css_selector_override=None,
        last_polled_at=None,
        last_signal_at=None,
        user_id=_UID,
    )
    return EmailImapSource(src, client_factory=lambda conn: fake)


def test_fetch_thread_headers_returns_threading_fields(monkeypatch):
    fake = _FakeImap(
        fields={
            "Subject": "Lunch?",
            "Message-ID": "<orig@x>",
            "References": "<a@x> <b@x>",
            "In-Reply-To": "<b@x>",
        }
    )
    adapter = _adapter(monkeypatch, fake)
    uidvalidity, headers = adapter.fetch_thread_headers(42)
    assert uidvalidity == 100
    assert headers["Message-ID"] == "<orig@x>"
    assert headers["References"] == "<a@x> <b@x>"
    assert headers["In-Reply-To"] == "<b@x>"
    # Read-only: INBOX selected readonly, no writes.
    assert fake.selects == [("INBOX", True)]


def test_fetch_thread_headers_miss_returns_none(monkeypatch):
    fake = _FakeImap(present=False)  # uid gone (moved/trashed)
    adapter = _adapter(monkeypatch, fake)
    uidvalidity, headers = adapter.fetch_thread_headers(42)
    assert headers is None


# --------------------------------------------------------------------------- #
# Reply-handler wiring
# --------------------------------------------------------------------------- #
class _FakeConn:
    host = "smtp.gmail.com"
    port = 587
    username = "alice@gmail.com"
    password = "app-pw"
    auth_type = "app_password"
    oauth_access_token = None


class _FakeStore:
    def __init__(self):
        self.sent_log: list = []

    def fetch_one(self, q, params=None):
        ql = (q or "").lower()
        if "select url from signals" in ql:
            return {"url": _IMAP_URL}
        if "from_addr, title from signals" in ql:
            return {"from_addr": "bob@x.com", "title": "Lunch?"}
        return None

    def fetch_all(self, q, params=None):
        return []

    def execute(self, q, params=None):
        if "insert into email_sent_log" in (q or "").lower():
            self.sent_log.append(params)


@pytest.fixture
def reply_env(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)
    monkeypatch.setattr(smtp_send, "_resolve_smtp", lambda sid, uid: _FakeConn())
    monkeypatch.setattr(smtp_send, "_load_owned_source", lambda sid, uid: object())
    sent: dict = {}

    def _fake_send(self, msg, **kw):
        sent["msg"] = msg
        return SendResult(message_id=msg["Message-ID"] or "<m@x>", refused=())

    monkeypatch.setattr(SmtpSender, "send", _fake_send, raising=True)
    return sent


def test_reply_sets_references_and_in_reply_to(reply_env, monkeypatch):
    monkeypatch.setattr(
        smtp_send,
        "_refetch_original",
        lambda src, uv, uid: ("Original", "<orig@x>", "<a@x> <b@x> <orig@x>"),
    )
    res = smtp_send._email_reply({"signal_id": "sig-1", "body": "sure"}, user_id=_UID)
    assert res.success
    msg = reply_env["msg"]
    # RFC order: References ends with the parent Message-ID; In-Reply-To equals it.
    assert msg["References"].endswith("<orig@x>")
    assert msg["In-Reply-To"] == "<orig@x>"


def test_reply_refetch_miss_no_threading_headers(reply_env, monkeypatch):
    monkeypatch.setattr(smtp_send, "_refetch_original", lambda src, uv, uid: (None, None, None))
    res = smtp_send._email_reply({"signal_id": "sig-1", "body": "x"}, user_id=_UID)
    assert res.success
    msg = reply_env["msg"]
    assert "References" not in msg
    assert "In-Reply-To" not in msg
    assert msg["Subject"] == "Re: Lunch?"  # from the signal title


def test_reply_original_without_message_id_omits_threading(reply_env, monkeypatch):
    # Original has a subject but no Message-ID → no threading headers, still sends.
    monkeypatch.setattr(
        smtp_send, "_refetch_original", lambda src, uv, uid: ("Original", None, None)
    )
    res = smtp_send._email_reply({"signal_id": "sig-1", "body": "x"}, user_id=_UID)
    assert res.success
    msg = reply_env["msg"]
    assert "References" not in msg and "In-Reply-To" not in msg


# ---------------------------------------------------------------------------
# LUM-705 — the widened header set actually yields To/Cc
# ---------------------------------------------------------------------------
#
# This is the ONLY test that can catch the widening silently not working, and the
# reason it had to be written is worth stating: `_parse_header_fields` falls back
# to ANY key containing `HEADER.FIELDS` when the exact `_THREAD_RESP_KEY` misses.
# So the fake above — keyed on the OLD literal bytes — still resolves after the
# widening and hands back the OLD block: no To, no Cc, no crash, and all 18
# pre-existing tests green. "Run the suite before and after" proves nothing here.
#
# The fixture below therefore keys on the CURRENT `_THREAD_RESP_KEY`, derived from
# the constant rather than hardcoded, and asserts real values come out.


class _RealisticHeaderClient:
    """Answers with a byte-accurate header block under the CURRENT response key."""

    def __init__(self, block: bytes, uidvalidity: int = 42) -> None:
        self._block = block
        self.uidvalidity = uidvalidity
        self.fetch_keys: list = []

    def select_folder(self, name, readonly=False):
        return {b"UIDVALIDITY": self.uidvalidity}

    def fetch(self, uids, keys):
        from adapters.email_imap_source import _THREAD_RESP_KEY

        self.fetch_keys.append(keys)
        return {uids[0]: {_THREAD_RESP_KEY: self._block}}

    def logout(self):
        pass


_REALISTIC_BLOCK = (
    b"Subject: Re: school trip\r\n"
    b"Message-ID: <orig@school.edu>\r\n"
    b"References: <a@x> <b@x>\r\n"
    b"In-Reply-To: <b@x>\r\n"
    # Folded across lines, and a display name containing a comma — the two shapes
    # a naive parser gets wrong.
    b'To: parent1@example.com,\r\n "Doe, Jane" <jane@example.com>\r\n'
    b"Cc: teacher@school.edu, head@school.edu\r\n"
    b"\r\n"
)


def test_fetch_thread_headers_returns_the_originals_to_and_cc(monkeypatch):
    fake = _RealisticHeaderClient(_REALISTIC_BLOCK)
    adapter = _adapter(monkeypatch, fake)

    uidvalidity, headers = adapter.fetch_thread_headers(7)

    assert uidvalidity == 42
    assert headers is not None
    assert "parent1@example.com" in headers["To"]
    assert "jane@example.com" in headers["To"]
    assert headers["Cc"] == "teacher@school.edu, head@school.edu"


def test_the_existing_threading_fields_still_parse_after_the_widening(monkeypatch):
    fake = _RealisticHeaderClient(_REALISTIC_BLOCK)
    adapter = _adapter(monkeypatch, fake)

    _, headers = adapter.fetch_thread_headers(7)

    assert headers["Subject"] == "Re: school trip"
    assert headers["Message-ID"] == "<orig@school.edu>"
    assert headers["In-Reply-To"] == "<b@x>"


def test_only_the_named_headers_are_requested(monkeypatch):
    """The security boundary: named fields only, never a bulk header copy."""
    fake = _RealisticHeaderClient(_REALISTIC_BLOCK)
    adapter = _adapter(monkeypatch, fake)

    adapter.fetch_thread_headers(7)

    key = fake.fetch_keys[0][0]
    assert key.startswith("BODY.PEEK[HEADER.FIELDS (")
    for field in ("SUBJECT", "MESSAGE-ID", "REFERENCES", "IN-REPLY-TO", "TO", "CC"):
        assert field in key
    # A bulk copy would read an injected header out of a crafted original.
    assert key != "BODY.PEEK[HEADER]"


def test_the_read_path_connects_with_a_timeout(monkeypatch):
    """A refused connection fails fast; a HUNG one would block this synchronous,
    user-triggered route forever and the caller's degrade would never run."""
    fake = _RealisticHeaderClient(_REALISTIC_BLOCK)
    adapter = _adapter(monkeypatch, fake)
    seen: dict = {}

    original = adapter._connect

    def _spy(*args, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return fake

    monkeypatch.setattr(adapter, "_connect", _spy)
    adapter.fetch_thread_headers(7)
    assert original is not None
    assert seen["timeout"] is not None and seen["timeout"] > 0
