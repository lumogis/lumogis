# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Handler + first-contact-gate tests for SMTP send (LUM-650).

Uses an in-memory fake metadata store (no Postgres) + monkeypatched credential /
transport seams. Covers:

* ``email_compose`` / ``email_reply`` happy paths + sent-log write (From = the
  authenticated account, never the payload).
* Reply re-fetch (subject + In-Reply-To) AND the graceful fallback when the
  original was moved/trashed by LUM-645/646.
* Ownership gate: reply on a shared (non-owned) signal → not_found.
* Send-not-enabled → send_not_enabled.
* First-contact: the create-time annotation (fail-closed) + the Do→Ask gate that
  refuses auto-approve for a send to a new address even in DO mode.
* ``email_send`` is not hard-limited.
"""

from __future__ import annotations

import pytest
from actions.errors import PermanentActionError
from actions.handlers import smtp_send

import config

_UID = "user-A"
_SID = "11111111-1111-1111-1111-111111111111"
_IMAP_URL = f"imap://{_SID}/100/42"


class _FakeConn:
    host = "smtp.gmail.com"
    port = 587
    username = "alice@gmail.com"
    password = "app-pw"
    auth_type = "app_password"
    oauth_access_token = None


class _FakeStore:
    """Answers only the queries these tests exercise; records writes."""

    def __init__(self, *, received=(), sent=(), signal=None, sent_cc=(), sent_bcc=()):
        self.received = [r.lower() for r in received]  # from_addr the user got mail from
        self.sent = [s.lower() for s in sent]  # addrs the user sent to (To)
        # LUM-698: cc IS part of the membership set; bcc deliberately is NOT, so
        # `sent_bcc` is modelled but never returned by the membership query.
        self.sent_cc = [s.lower() for s in sent_cc]
        self.sent_bcc = [s.lower() for s in sent_bcc]
        self.signal = signal or {"url": _IMAP_URL, "from_addr": "bob@x.com", "title": "Lunch?"}
        self.sent_log: list[dict] = []
        self.proposals: list[dict] = []
        self._nid = 0

    def fetch_one(self, q, params=None):
        ql = (q or "").lower()
        p = params or ()
        if "select url from signals" in ql:
            return {"url": self.signal["url"]}
        if "from_addr, title from signals" in ql:
            return {"from_addr": self.signal["from_addr"], "title": self.signal["title"]}
        if "insert into action_proposals" in ql:
            self._nid += 1
            # params: (user_id, action_name, payload_json, annotation_json, ...)
            self.proposals.append({"id": self._nid, "annotation": p[3]})
            return {"id": self._nid}
        return None

    def fetch_all(self, q, params=None):
        ql = (q or "").lower()
        p = params or ()
        targets = set(p[1]) if len(p) > 1 and isinstance(p[1], (list, tuple)) else set()
        if "from signals" in ql and "lower(from_addr)" in ql:
            return [{"a": a} for a in self.received if a in targets]
        if "from email_sent_log" in ql and "unnest(to_addrs || cc_addrs)" in ql:
            # Mirrors the real SQL: to+cc participate, bcc never does.
            pool = [*self.sent, *self.sent_cc]
            return [{"a": a} for a in pool if a in targets]
        return []

    def execute(self, q, params=None):
        ql = (q or "").lower()
        p = params or ()
        if "insert into email_sent_log" in ql:
            self.sent_log.append(
                {
                    "user_id": p[0],
                    "source_id": p[1],
                    "to_addrs": p[2],
                    "subject": p[3],
                    "message_id": p[4],
                    "in_reply_to": p[5],
                    "action": p[6],
                    "cc_addrs": p[7] if len(p) > 7 else [],
                    "bcc_addrs": p[8] if len(p) > 8 else [],
                    "proposal_id": p[9] if len(p) > 9 else None,
                }
            )


@pytest.fixture
def store(monkeypatch):
    s = _FakeStore()
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)
    return s


@pytest.fixture
def stub_send(monkeypatch):
    """Stub the transport so happy-path handlers don't touch a real SMTP server;
    keep build_message real (so injection/From invariants are exercised)."""
    sent: dict = {}

    from adapters.email_smtp_sender import SendResult
    from adapters.email_smtp_sender import SmtpSender

    def _fake_send(
        self,
        msg,
        *,
        host,
        port,
        username,
        app_password,
        user_id=None,
        auth_type="app_password",
        access_token=None,
    ):
        sent["msg"] = msg
        sent["host"] = host
        sent["username"] = username
        sent["auth_type"] = auth_type
        sent["access_token"] = access_token
        return SendResult(message_id=msg["Message-ID"] or "<mid@x>", refused=())

    monkeypatch.setattr(SmtpSender, "send", _fake_send, raising=True)
    monkeypatch.setattr(smtp_send, "_resolve_smtp", lambda sid, uid: _FakeConn())
    monkeypatch.setattr(smtp_send, "_load_owned_source", lambda sid, uid: object())
    return sent


# --------------------------------------------------------------------------- #
# compose / reply happy paths
# --------------------------------------------------------------------------- #
def test_compose_sends_and_logs(store, stub_send):
    res = smtp_send._email_compose(
        {"source_id": _SID, "to": ["carol@y.com"], "subject": "Hello", "body": "hi"},
        user_id=_UID,
    )
    assert res.success
    # From is the authenticated account, never the payload (no `from` field exists).
    assert stub_send["msg"]["From"] == "alice@gmail.com"
    assert stub_send["msg"]["To"] == "carol@y.com"
    assert len(store.sent_log) == 1
    assert store.sent_log[0]["action"] == "compose"
    assert store.sent_log[0]["to_addrs"] == ["carol@y.com"]


@pytest.mark.parametrize(
    "handler, payload",
    [
        (
            smtp_send._email_compose,
            {"source_id": _SID, "to": ["carol@y.com"], "subject": "Hello", "body": "hi"},
        ),
        (
            smtp_send._email_reply,
            {"signal_id": "sig-1", "body": "sure"},
        ),
        (
            smtp_send._email_forward,
            {"signal_id": "sig-1", "to": ["carol@y.com"], "body": "fyi"},
        ),
    ],
)
def test_send_handlers_link_sent_log_to_proposal_id(
    store, stub_send, monkeypatch, handler, payload
):
    """LUM-793: proposal_id must reach email_sent_log so recovery can resolve reply/forward."""
    if handler is smtp_send._email_reply or handler is smtp_send._email_forward:
        monkeypatch.setattr(
            smtp_send, "_refetch_original", lambda src, uv, uid: ("Orig", "<m@x>", None)
        )
    proposal_id = 4242
    res = handler(payload, user_id=_UID, proposal_id=proposal_id)
    assert res.success
    assert store.sent_log[0]["proposal_id"] == proposal_id


def test_reply_uses_refetched_subject_and_in_reply_to(store, stub_send, monkeypatch):
    # LUM-651: _refetch_original now returns (subject, message_id, references).
    monkeypatch.setattr(
        smtp_send,
        "_refetch_original",
        lambda src, uv, uid: ("Original Subject", "<orig@x.com>", None),
    )
    res = smtp_send._email_reply({"signal_id": "sig-1", "body": "sure"}, user_id=_UID)
    assert res.success
    assert stub_send["msg"]["Subject"] == "Re: Original Subject"
    assert stub_send["msg"]["In-Reply-To"] == "<orig@x.com>"
    assert stub_send["msg"]["To"] == "bob@x.com"  # the original sender
    assert store.sent_log[0]["action"] == "reply"


def test_reply_refetch_miss_falls_back(store, stub_send, monkeypatch):
    # The original was moved/trashed by LUM-645/646 → re-fetch misses.
    # LUM-651: _refetch_original now returns a 3-tuple.
    monkeypatch.setattr(smtp_send, "_refetch_original", lambda src, uv, uid: (None, None, None))
    res = smtp_send._email_reply({"signal_id": "sig-1", "body": "sure"}, user_id=_UID)
    assert res.success
    # Subject derived from the signal's stored title; no In-Reply-To → no hard fail.
    assert stub_send["msg"]["Subject"] == "Re: Lunch?"
    assert "In-Reply-To" not in stub_send["msg"]


def test_partial_refusal_logs_accepted_and_fails(store, monkeypatch):
    # A partial refusal: the accepted recipient IS logged (audit truth + counts as
    # contacted), and the action fails permanently naming the refused address.
    from adapters.email_smtp_sender import SendResult
    from adapters.email_smtp_sender import SmtpSender

    monkeypatch.setattr(smtp_send, "_resolve_smtp", lambda sid, uid: _FakeConn())
    monkeypatch.setattr(smtp_send, "_load_owned_source", lambda sid, uid: object())
    monkeypatch.setattr(
        SmtpSender,
        "send",
        lambda self, msg, **kw: SendResult(message_id="<m@x>", refused=("bad@y.com",)),
        raising=True,
    )
    with pytest.raises(PermanentActionError) as ei:
        smtp_send._email_compose(
            {"source_id": _SID, "to": ["ok@y.com", "bad@y.com"], "subject": "s", "body": "b"},
            user_id=_UID,
        )
    assert ei.value.code == "recipients_refused"
    # The accepted recipient was still logged; the refused one was not.
    assert store.sent_log[0]["to_addrs"] == ["ok@y.com"]


def test_invalid_recipient_rejected_at_validation():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        smtp_send.EmailComposeInput.model_validate(
            {"source_id": _SID, "to": ["not-an-email"], "subject": "s", "body": "b"}
        )
    # CR/LF header-injection attempt is also rejected at the input layer.
    with pytest.raises(ValidationError):
        smtp_send.EmailComposeInput.model_validate(
            {"source_id": _SID, "to": ["a@x.com\r\nBcc: e@x.com"], "subject": "s", "body": "b"}
        )


def test_reply_on_shared_signal_denied(store, monkeypatch):
    def _deny(sid, uid):
        raise PermanentActionError("not_found", "not owned")

    monkeypatch.setattr(smtp_send, "_load_owned_source", _deny)
    with pytest.raises(PermanentActionError) as ei:
        smtp_send._email_reply({"signal_id": "sig-1", "body": "x"}, user_id=_UID)
    assert ei.value.code == "not_found"


def test_send_not_enabled_refused(store, monkeypatch):
    monkeypatch.setattr(smtp_send, "_load_owned_source", lambda sid, uid: object())
    from services import connector_credentials as ccs
    from services import email_credentials

    def _raise(uid, *, instance):
        raise ccs.ConnectorNotConfigured("no smtp row")

    monkeypatch.setattr(email_credentials, "load_smtp_connection", _raise)
    with pytest.raises(PermanentActionError) as ei:
        smtp_send._email_compose(
            {"source_id": _SID, "to": ["c@y.com"], "subject": "s", "body": "b"}, user_id=_UID
        )
    assert ei.value.code == "send_not_enabled"


# --------------------------------------------------------------------------- #
# _resolve_smtp error boundary (LUM-686): xoauth2 OAuth failures degrade to a
# PermanentActionError; app-password CredentialUnavailable still propagates RAW.
# --------------------------------------------------------------------------- #
def test_resolve_smtp_xoauth2_refresh_failure_is_permanent(monkeypatch):
    from services.email_oauth import OAuthRefreshError

    from services import email_credentials

    def _raise(uid, *, instance):
        raise OAuthRefreshError("token refresh failed (400) for google")

    monkeypatch.setattr(email_credentials, "load_smtp_connection", _raise)
    with pytest.raises(PermanentActionError) as ei:
        smtp_send._resolve_smtp(_SID, _UID)
    assert ei.value.code == "smtp_auth_failed"


def test_resolve_smtp_xoauth2_none_token_guard_is_permanent(monkeypatch):
    # The None-token guard also raises OAuthRefreshError → smtp_auth_failed.
    from services.email_oauth import OAuthRefreshError

    from services import email_credentials

    def _raise(uid, *, instance):
        raise OAuthRefreshError("xoauth2 smtp: IMAP connection carried no access token")

    monkeypatch.setattr(email_credentials, "load_smtp_connection", _raise)
    with pytest.raises(PermanentActionError) as ei:
        smtp_send._resolve_smtp(_SID, _UID)
    assert ei.value.code == "smtp_auth_failed"


def test_resolve_smtp_app_password_credential_unavailable_propagates_raw(monkeypatch):
    # The narrow OAuthRefreshError catch must NOT swallow a pre-existing
    # app-password CredentialUnavailable (Fernet decrypt / malformed payload) —
    # it keeps propagating raw (retry-with-backoff), byte-for-byte unchanged.
    from services import connector_credentials as ccs
    from services import email_credentials

    def _raise(uid, *, instance):
        raise ccs.CredentialUnavailable("substrate decrypt failed")

    monkeypatch.setattr(email_credentials, "load_smtp_connection", _raise)
    with pytest.raises(ccs.CredentialUnavailable):
        smtp_send._resolve_smtp(_SID, _UID)


def test_resolve_smtp_xoauth2_conn_forwarded_to_sender(store, monkeypatch):
    # The handler forwards conn.auth_type / conn.oauth_access_token to sender.send.
    from adapters.email_smtp_sender import SendResult
    from adapters.email_smtp_sender import SmtpSender

    class _XoauthConn:
        host = "smtp.gmail.com"
        port = 587
        username = "alice@gmail.com"
        password = ""
        auth_type = "xoauth2"
        oauth_access_token = "ACCESS-TOK"

    seen: dict = {}

    def _fake_send(
        self,
        msg,
        *,
        host,
        port,
        username,
        app_password,
        user_id=None,
        auth_type="app_password",
        access_token=None,
    ):
        seen["auth_type"] = auth_type
        seen["access_token"] = access_token
        return SendResult(message_id="<m@x>", refused=())

    monkeypatch.setattr(SmtpSender, "send", _fake_send, raising=True)
    monkeypatch.setattr(smtp_send, "_resolve_smtp", lambda sid, uid: _XoauthConn())
    monkeypatch.setattr(smtp_send, "_load_owned_source", lambda sid, uid: object())
    res = smtp_send._email_compose(
        {"source_id": _SID, "to": ["c@y.com"], "subject": "s", "body": "b"}, user_id=_UID
    )
    assert res.success
    assert seen == {"auth_type": "xoauth2", "access_token": "ACCESS-TOK"}


# --------------------------------------------------------------------------- #
# first-contact detection + annotation (fail-closed)
# --------------------------------------------------------------------------- #
def test_first_contact_true_for_unknown(monkeypatch):
    s = _FakeStore(received=["bob@x.com"], sent=["dave@z.com"])
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)
    assert smtp_send._is_first_contact(_UID, ["new@stranger.com"]) is True


def test_first_contact_false_for_known_normalized(monkeypatch):
    s = _FakeStore(received=["bob@x.com"], sent=["dave@z.com"])
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)
    # Display name + different case still matches the known bare addr.
    assert smtp_send._is_first_contact(_UID, ["Bob Example <BOB@X.com>"]) is False
    assert smtp_send._is_first_contact(_UID, ["dave@z.com"]) is False


def test_annotate_compose_unknown_is_first_contact(monkeypatch):
    s = _FakeStore(received=[], sent=[])
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)
    ann = smtp_send._annotate_send({"to": ["new@x.com"], "subject": "s", "body": "b"}, _UID)
    assert ann == {"first_contact": True}


def test_annotate_fail_closed_on_missing_signal(monkeypatch):
    # Reply annotation with no resolvable recipient (signal lookup returns no
    # from_addr) must fail CLOSED → force Ask.
    s = _FakeStore(signal={"url": _IMAP_URL, "from_addr": "", "title": "t"})
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)
    ann = smtp_send._annotate_send({"signal_id": "sig-x", "body": "b"}, _UID)
    assert ann == {"first_contact": True}


def test_annotate_fail_closed_on_error(monkeypatch):
    def _boom(uid, addrs):
        raise RuntimeError("db down")

    monkeypatch.setattr(smtp_send, "_is_first_contact", _boom)
    monkeypatch.setattr(smtp_send, "_recipients_for_annotation", lambda p, uid: ["x@y.com"])
    ann = smtp_send._annotate_send({"to": ["x@y.com"], "subject": "s", "body": "b"}, "u")
    assert ann == {"first_contact": True}


# --------------------------------------------------------------------------- #
# first-contact annotate — reply recipient resolution is OWNER-SCOPED (LUM-652
# security review): first_contact is now on the wire, so it must not become an
# oracle on a signal_id the caller does not own.
# --------------------------------------------------------------------------- #
class _OwnerScopedStore:
    def __init__(self, owner: str):
        self.owner = owner

    def fetch_one(self, q, params=None):
        ql = (q or "").lower()
        p = params or ()
        if "from_addr from signals where signal_id" in ql and "user_id" in ql:
            _sig_id, uid = p[0], p[1]
            return {"from_addr": "bob@x.com"} if uid == self.owner else None
        return None


def test_recipients_for_annotation_reply_scoped_to_owned_signal(monkeypatch):
    monkeypatch.setattr(config, "get_metadata_store", lambda: _OwnerScopedStore(owner="me"))
    # Owned signal → the original sender resolves.
    assert smtp_send._recipients_for_annotation({"signal_id": "s", "body": "b"}, "me") == [
        "bob@x.com"
    ]
    # A signal the caller does not own → no recipient (no cross-user from_addr touch).
    assert smtp_send._recipients_for_annotation({"signal_id": "s", "body": "b"}, "intruder") == []


def test_annotate_reply_non_owned_signal_fails_closed(monkeypatch):
    monkeypatch.setattr(config, "get_metadata_store", lambda: _OwnerScopedStore(owner="me"))
    # A non-owned reply signal → fail closed (first_contact True), never a leaked bit.
    assert smtp_send._annotate_send({"signal_id": "s", "body": "b"}, "intruder") == {
        "first_contact": True
    }


# --------------------------------------------------------------------------- #
# The Do→Ask gate — end to end through create_proposal + executor.execute
# --------------------------------------------------------------------------- #
def test_email_send_is_not_hard_limited():
    from actions.executor import is_hard_limited

    assert is_hard_limited("email_send") is False


def test_annotate_persisted_at_create(monkeypatch):
    import json

    s = _FakeStore(received=[], sent=[])  # unknown recipient
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)
    from services import action_proposals

    pid = action_proposals.create_proposal(
        "email_compose",
        {"source_id": _SID, "to": ["stranger@x.com"], "subject": "s", "body": "b"},
        user_id=_UID,
    )
    assert pid == s.proposals[0]["id"]
    stored = json.loads(s.proposals[0]["annotation"])
    assert stored == {"first_contact": True}


def test_first_contact_forces_ask_in_do_mode(monkeypatch):
    """The load-bearing security test: with smtp in DO mode, a compose to an
    UNKNOWN address is refused when executed without an explicit approval
    (auto-Do), but a compose to a KNOWN address auto-executes. Drives the real
    create→annotation-computed→execute path, not a hand-set flag."""
    import permissions
    from actions.executor import execute

    monkeypatch.setattr(permissions, "get_connector_mode", lambda *, user_id, connector: "DO")
    monkeypatch.setattr(permissions, "routine_check", lambda *a, **k: None)
    monkeypatch.setattr(smtp_send, "_resolve_smtp", lambda sid, uid: _FakeConn())
    monkeypatch.setattr(smtp_send, "_load_owned_source", lambda sid, uid: object())
    from adapters.email_smtp_sender import SendResult
    from adapters.email_smtp_sender import SmtpSender

    monkeypatch.setattr(
        SmtpSender,
        "send",
        lambda self, msg, **kw: SendResult(message_id=msg["Message-ID"] or "<m@x>", refused=()),
        raising=True,
    )

    # Unknown recipient → first_contact → DO must NOT auto-approve (force Ask).
    s_unknown = _FakeStore(received=[], sent=[])
    monkeypatch.setattr(config, "get_metadata_store", lambda: s_unknown)
    res = execute(
        "email_compose",
        {"source_id": _SID, "to": ["stranger@x.com"], "subject": "s", "body": "b"},
        user_id=_UID,
        approved=False,
    )
    assert res.success is False
    assert "Permission denied" in (res.error or "")

    # Known recipient → not first_contact → DO auto-executes (no force Ask).
    s_known = _FakeStore(received=["friend@x.com"], sent=[])
    monkeypatch.setattr(config, "get_metadata_store", lambda: s_known)
    res2 = execute(
        "email_compose",
        {"source_id": _SID, "to": ["friend@x.com"], "subject": "s", "body": "b"},
        user_id=_UID,
        approved=False,
    )
    assert res2.success is True


def test_first_contact_stored_annotation_forces_ask_even_if_recompute_would_differ(monkeypatch):
    """The claimed-proposal path reads the STORED annotation, not a recompute:
    passing a stored first_contact=true annotation forces Ask under DO even when
    the recipient later looks 'known'."""
    import permissions
    from actions.executor import execute

    monkeypatch.setattr(permissions, "get_connector_mode", lambda *, user_id, connector: "DO")
    monkeypatch.setattr(permissions, "routine_check", lambda *a, **k: None)
    s = _FakeStore(received=["friend@x.com"], sent=[])  # would recompute as known
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)

    res = execute(
        "email_compose",
        {"source_id": _SID, "to": ["friend@x.com"], "subject": "s", "body": "b"},
        user_id=_UID,
        approved=False,
        annotation={"first_contact": True},  # stored decision wins
    )
    assert res.success is False
    assert "Permission denied" in (res.error or "")


# --------------------------------------------------------------------------- #
# LUM-698 — CC/BCC, forward, first-contact membership, sent-log, caps
# --------------------------------------------------------------------------- #
def test_compose_sends_cc_and_bcc_and_logs_them_separately(store, stub_send):
    res = smtp_send._email_compose(
        {
            "source_id": _SID,
            "to": ["carol@y.com"],
            "subject": "Hello",
            "body": "hi",
            "cc": ["dan@y.com"],
            "bcc": ["eve@z.com"],
        },
        user_id=_UID,
    )
    assert res.success
    assert stub_send["msg"]["Cc"] == "dan@y.com"
    assert stub_send["msg"]["Bcc"] == "eve@z.com"
    row = store.sent_log[0]
    # Kept DISTINCT — flattening would misreport the send AND leak bcc into the
    # set the first-contact check queries.
    assert row["to_addrs"] == ["carol@y.com"]
    assert row["cc_addrs"] == ["dan@y.com"]
    assert row["bcc_addrs"] == ["eve@z.com"]


def test_reply_accepts_cc_without_any_to_field(store, stub_send, monkeypatch):
    """A reply's primary recipient stays signal-derived; cc/bcc are the only
    client-set recipients on that shape."""
    monkeypatch.setattr(
        smtp_send, "_refetch_original", lambda src, uv, uid: ("Orig", "<m@x>", None)
    )
    res = smtp_send._email_reply(
        {"signal_id": "sig-1", "body": "sure", "cc": ["dan@y.com"]}, user_id=_UID
    )
    assert res.success
    assert stub_send["msg"]["To"] == "bob@x.com"  # from the signal, not the payload
    assert stub_send["msg"]["Cc"] == "dan@y.com"
    assert store.sent_log[0]["cc_addrs"] == ["dan@y.com"]


def test_forward_starts_a_new_thread_without_threading_headers(store, stub_send, monkeypatch):
    monkeypatch.setattr(
        smtp_send, "_refetch_original", lambda src, uv, uid: ("Orig", "<m@x>", "<r@x>")
    )
    res = smtp_send._email_forward(
        {"signal_id": "sig-1", "to": ["carol@y.com"], "body": "fyi"}, user_id=_UID
    )
    assert res.success
    assert stub_send["msg"]["Subject"] == "Fwd: Orig"
    # A forward is NOT a reply — threading headers must be absent even though
    # _refetch_original supplied them.
    assert stub_send["msg"]["In-Reply-To"] is None
    assert stub_send["msg"]["References"] is None
    assert store.sent_log[0]["action"] == "forward"
    assert store.sent_log[0]["in_reply_to"] is None


def test_forward_never_spoofs_the_original_sender(store, stub_send, monkeypatch):
    monkeypatch.setattr(smtp_send, "_refetch_original", lambda src, uv, uid: ("Orig", None, None))
    smtp_send._email_forward(
        {"signal_id": "sig-1", "to": ["carol@y.com"], "body": ""}, user_id=_UID
    )
    # From is our authenticated account, never the original sender (bob@x.com).
    assert stub_send["msg"]["From"] == "alice@gmail.com"


@pytest.mark.parametrize(
    "original,expected",
    [
        ("Orig", "Fwd: Orig"),
        ("Fwd: Orig", "Fwd: Orig"),
        ("FW: Orig", "FW: Orig"),
        ("", "Fwd:"),
    ],
)
def test_forward_subject_prefix_is_idempotent(original, expected):
    assert smtp_send._fwd_subject(original) == expected


def test_forward_degrades_when_original_is_gone(store, stub_send, monkeypatch):
    """LUM-645/646 may have moved/trashed the original — subject falls back to
    the stored signal title rather than hard-failing."""
    monkeypatch.setattr(smtp_send, "_refetch_original", lambda src, uv, uid: (None, None, None))
    res = smtp_send._email_forward(
        {"signal_id": "sig-1", "to": ["carol@y.com"], "body": "fyi"}, user_id=_UID
    )
    assert res.success
    assert stub_send["msg"]["Subject"] == "Fwd: Lunch?"  # the signal's title


def test_first_contact_covers_cc_and_bcc_recipients(monkeypatch):
    """A known `to` plus an UNKNOWN bcc must still force Ask."""
    s = _FakeStore(received=["bob@x.com"], sent=[])
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)
    ann = smtp_send._annotate_send({"to": ["bob@x.com"], "bcc": ["stranger@z.com"]}, _UID)
    assert ann["first_contact"] is True


def test_previously_cc_d_address_is_not_first_contact(monkeypatch):
    """A past CC counts as contacted (unnest(to_addrs || cc_addrs))."""
    s = _FakeStore(sent=[], sent_cc=["dan@y.com"])
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)
    assert smtp_send._is_first_contact(_UID, ["dan@y.com"]) is False


def test_previously_bcc_d_address_is_still_first_contact(monkeypatch):
    """A past BCC deliberately does NOT count — excluding bcc from the
    membership set is what keeps it from becoming a side channel. Accepted
    cost: a BCC-only correspondent forces Ask forever."""
    s = _FakeStore(sent=[], sent_bcc=["eve@z.com"])
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)
    assert smtp_send._is_first_contact(_UID, ["eve@z.com"]) is True


def test_partial_refusal_of_a_cc_recipient_is_surfaced(store, monkeypatch):
    """Regression guard for the union-based _finalize_send: a refused CC must
    not be silently dropped from the error or the accepted-set logging."""
    from adapters.email_smtp_sender import SendResult

    result = SendResult(message_id="<m@x>", refused=("dan@y.com",))
    with pytest.raises(PermanentActionError) as ei:
        smtp_send._finalize_send(
            result=result,
            user_id=_UID,
            source_id=_SID,
            to_addrs=["carol@y.com"],
            subject="s",
            in_reply_to=None,
            action="compose",
            cc_addrs=["dan@y.com"],
            bcc_addrs=["eve@z.com"],
        )
    assert ei.value.code == "recipients_refused"
    assert "dan@y.com" in str(ei.value)
    # The accepted recipients still received it, so they are still logged.
    row = store.sent_log[0]
    assert row["to_addrs"] == ["carol@y.com"]
    assert row["cc_addrs"] == []  # refused → excluded
    assert row["bcc_addrs"] == ["eve@z.com"]


@pytest.mark.parametrize("field", ["cc", "bcc"])
def test_malformed_cc_bcc_rejected_at_create(field):
    from pydantic import ValidationError

    payload = {"source_id": _SID, "to": ["a@x.com"], "subject": "s", "body": "b", field: ["nope"]}
    with pytest.raises(ValidationError):
        smtp_send.EmailComposeInput.model_validate(payload)


def test_recipient_cap_rejected_at_create():
    from pydantic import ValidationError

    many = [f"user{i}@x.com" for i in range(101)]
    with pytest.raises(ValidationError):
        smtp_send.EmailComposeInput.model_validate(
            {"source_id": _SID, "to": many, "subject": "s", "body": "b"}
        )
    # Also across the union, not just `to`.
    with pytest.raises(ValidationError):
        smtp_send.EmailComposeInput.model_validate(
            {
                "source_id": _SID,
                "to": ["a@x.com"],
                "subject": "s",
                "body": "b",
                "bcc": [f"u{i}@x.com" for i in range(100)],
            }
        )


def test_email_forward_registered():
    from actions.registry import get_action

    spec = get_action("email_forward")
    assert spec is not None
    assert spec.connector == "smtp"
    assert spec.is_write is True
    assert spec.is_reversible is False  # you cannot unsend
    assert spec.annotate is not None  # first-contact force-Ask applies


# --------------------------------------------------------------------------- #
# LUM-695 — all four send paths carry attachments through the ONE shared builder
# --------------------------------------------------------------------------- #
@pytest.fixture
def staged_file(monkeypatch):
    """One resolvable staged attachment, plus a record of what got discarded."""
    from services import email_attachment_staging as staging

    class _Path:
        @staticmethod
        def read_bytes():
            return b"PDFBYTES"

        @staticmethod
        def exists():
            return True

    class _Resolved:
        id = "staged-1"
        filename = "invoice.pdf"
        mime_type = "application/pdf"
        path = _Path()

    discarded: list[list[str]] = []
    monkeypatch.setattr(staging, "resolve_all", lambda uid, ids: [_Resolved()])
    monkeypatch.setattr(staging, "discard", lambda uid, ids: discarded.append(list(ids)))
    return discarded


def _attached_files(msg) -> dict:
    """{filename: bytes} as the recipient would parse them off the wire."""
    from email import message_from_bytes

    parsed = message_from_bytes(msg.as_bytes())
    return {p.get_filename(): p.get_payload(decode=True) for p in parsed.walk() if p.get_filename()}


def test_compose_carries_attachments_into_the_sent_message(store, stub_send, staged_file):
    res = smtp_send._email_compose(
        {
            "source_id": _SID,
            "to": ["carol@y.com"],
            "subject": "Hello",
            "body": "hi",
            "attachment_ids": ["staged-1"],
        },
        user_id=_UID,
    )

    assert res.success
    assert stub_send["msg"].get_content_type() == "multipart/mixed"
    assert _attached_files(stub_send["msg"]) == {"invoice.pdf": b"PDFBYTES"}


def test_reply_carries_attachments_into_the_sent_message(
    store, stub_send, staged_file, monkeypatch
):
    monkeypatch.setattr(
        smtp_send, "_refetch_original", lambda src, uv, uid: ("Orig", "<o@x>", None)
    )

    res = smtp_send._email_reply(
        {"signal_id": "sig-1", "body": "sure", "attachment_ids": ["staged-1"]}, user_id=_UID
    )

    assert res.success
    assert _attached_files(stub_send["msg"]) == {"invoice.pdf": b"PDFBYTES"}
    # Threading must survive the structural change to multipart.
    assert stub_send["msg"]["In-Reply-To"] == "<o@x>"


def test_forward_carries_attachments_into_the_sent_message(
    store, stub_send, staged_file, monkeypatch
):
    monkeypatch.setattr(
        smtp_send, "_refetch_original", lambda src, uv, uid: ("Orig", "<o@x>", None)
    )

    res = smtp_send._email_forward(
        {
            "signal_id": "sig-1",
            "to": ["carol@y.com"],
            "body": "fyi",
            "attachment_ids": ["staged-1"],
        },
        user_id=_UID,
    )

    assert res.success
    assert _attached_files(stub_send["msg"]) == {"invoice.pdf": b"PDFBYTES"}
    # A forward still starts a new thread — the multipart path must not change that.
    assert stub_send["msg"]["In-Reply-To"] is None


def test_a_successful_send_discards_its_staged_files(store, stub_send, staged_file):
    """The fast path that returns the user's disk — the sweep is only the backstop."""
    smtp_send._email_compose(
        {
            "source_id": _SID,
            "to": ["carol@y.com"],
            "subject": "Hello",
            "body": "hi",
            "attachment_ids": ["staged-1"],
        },
        user_id=_UID,
    )

    assert staged_file == [["staged-1"]]


def test_a_send_without_attachments_stays_single_part(store, stub_send):
    """The no-attachments path is unchanged for every existing caller."""
    smtp_send._email_compose(
        {"source_id": _SID, "to": ["carol@y.com"], "subject": "Hello", "body": "hi"},
        user_id=_UID,
    )

    assert not stub_send["msg"].is_multipart()


# --------------------------------------------------------------------------- #
# LUM-712 — forwarding an ORIGINAL's attachments
# --------------------------------------------------------------------------- #
@pytest.fixture
def original_parts(monkeypatch):
    """The original message's own attachments, as the adapter would return them."""
    from services import email_attachments

    fetched: list[dict] = []

    class _Adapter:
        def __init__(self, source, **kw):
            pass

        def fetch_attachment_parts(self, **kw):
            fetched.append(kw)
            return [
                email_attachments.ResolvedPart(
                    filename="original.pdf",
                    mime_type="application/pdf",
                    data=b"ORIGINALBYTES",
                    part_index=int(i),
                )
                for i in kw["part_indexes"]
            ]

    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _Adapter)
    return fetched


def test_forward_carries_the_originals_attachments(store, stub_send, original_parts, monkeypatch):
    monkeypatch.setattr(
        smtp_send, "_refetch_original", lambda src, uv, uid: ("Orig", "<o@x>", None)
    )

    res = smtp_send._email_forward(
        {
            "signal_id": "sig-1",
            "to": ["carol@y.com"],
            "body": "fyi",
            "forward_part_indexes": [0],
        },
        user_id=_UID,
    )

    assert res.success
    assert _attached_files(stub_send["msg"]) == {"original.pdf": b"ORIGINALBYTES"}


def test_forwarding_originals_carries_indexes_not_bytes_in_the_payload() -> None:
    """The no-bytes-in-the-payload rule holds for re-attached originals too.

    A part INDEX is the natural reference here — the signal already identifies
    the message, so the handler can re-fetch at execute time exactly as it does
    for staged files.
    """
    parsed = smtp_send.EmailForwardInput(
        signal_id="s", to=["x@y.com"], body="b", forward_part_indexes=[0, 2]
    )

    assert parsed.forward_part_indexes == [0, 2]
    with pytest.raises(Exception):
        smtp_send.EmailForwardInput(
            signal_id="s",
            to=["x@y.com"],
            body="b",
            forward_parts=[{"filename": "a.pdf", "data": "QUJD"}],
        )


def test_forward_combines_staged_files_and_original_attachments(
    store, stub_send, original_parts, staged_file, monkeypatch
):
    monkeypatch.setattr(
        smtp_send, "_refetch_original", lambda src, uv, uid: ("Orig", "<o@x>", None)
    )

    smtp_send._email_forward(
        {
            "signal_id": "sig-1",
            "to": ["carol@y.com"],
            "body": "fyi",
            "attachment_ids": ["staged-1"],
            "forward_part_indexes": [0],
        },
        user_id=_UID,
    )

    assert _attached_files(stub_send["msg"]) == {
        "invoice.pdf": b"PDFBYTES",
        "original.pdf": b"ORIGINALBYTES",
    }


def test_forward_without_original_indexes_never_refetches_parts(
    store, stub_send, original_parts, monkeypatch
):
    """Unchanged behaviour for every forward written before LUM-712."""
    monkeypatch.setattr(
        smtp_send, "_refetch_original", lambda src, uv, uid: ("Orig", "<o@x>", None)
    )

    smtp_send._email_forward(
        {"signal_id": "sig-1", "to": ["carol@y.com"], "body": "fyi"}, user_id=_UID
    )

    assert original_parts == [], "no attachment re-fetch for a plain forward"
    assert not stub_send["msg"].is_multipart()


def test_a_lost_original_attachment_dead_letters_rather_than_sending_without_it(
    store, stub_send, monkeypatch
):
    """Same posture as a swept staged file: refuse, do not quietly drop a file."""
    from adapters.email_imap_source import AttachmentFetchError

    class _GoneAdapter:
        def __init__(self, source, **kw):
            pass

        def fetch_attachment_parts(self, **kw):
            raise AttachmentFetchError("message_gone", "original is gone")

    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _GoneAdapter)
    monkeypatch.setattr(
        smtp_send, "_refetch_original", lambda src, uv, uid: ("Orig", "<o@x>", None)
    )

    with pytest.raises(PermanentActionError) as exc:
        smtp_send._email_forward(
            {"signal_id": "sig-1", "to": ["c@y.com"], "body": "fyi", "forward_part_indexes": [0]},
            user_id=_UID,
        )

    assert exc.value.code == "attachment_unavailable"


def test_forward_draft_also_carries_the_originals_attachments(store, original_parts, monkeypatch):
    """A saved forward and a sent forward must remain the same message."""
    appended: dict = {}

    class _DraftAdapter:
        def __init__(self, source, **kw):
            self.conn = _FakeConn()

        def fetch_attachment_parts(self, **kw):
            from services import email_attachments

            return [
                email_attachments.ResolvedPart(
                    filename="original.pdf",
                    mime_type="application/pdf",
                    data=b"ORIGINALBYTES",
                    part_index=int(i),
                )
                for i in kw["part_indexes"]
            ]

        def append_to_drafts(self, mime_bytes, message_id):
            appended["bytes"] = mime_bytes
            return {"folder": "Drafts", "message_id": message_id}

    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _DraftAdapter)
    monkeypatch.setattr(
        smtp_send, "_refetch_original", lambda src, uv, uid: ("Orig", "<o@x>", None)
    )
    monkeypatch.setattr(smtp_send, "_load_owned_source", lambda sid, uid: object())

    res = smtp_send._email_save_draft(
        {"signal_id": "sig-1", "to": ["carol@y.com"], "body": "fyi", "forward_part_indexes": [0]},
        user_id=_UID,
    )

    assert res.success
    from email import message_from_bytes

    parsed = message_from_bytes(appended["bytes"])
    names = {
        p.get_filename(): p.get_payload(decode=True) for p in parsed.walk() if p.get_filename()
    }
    assert names == {"original.pdf": b"ORIGINALBYTES"}
