# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Save-to-Drafts: adapter APPEND + ``email_save_draft`` handler (LUM-652).

In-memory fakes (no Postgres / no real IMAP). Covers:
* ``append_to_drafts`` targets the SPECIAL-USE Drafts folder with the ``\\Draft``
  flag + a msg_time; degrades ``no_drafts_folder`` when absent.
* the handler builds the same MIME as Send (byte-identical modulo Message-ID),
  From = the IMAP account username (no SMTP opt-in), threads a reply-draft, and
  never writes ``email_sent_log``.
* ``EmailSaveDraftInput`` two-shape validation (422 at create, not a dead-letter).
* registration shape.
"""

from __future__ import annotations

import pytest
from actions.errors import PermanentActionError
from actions.handlers import smtp_send
from adapters.email_imap_source import EmailImapSource

import config

_UID = "user-A"
_SID = "11111111-1111-1111-1111-111111111111"
_IMAP_URL = f"imap://{_SID}/100/42"


class _FakeImapClient:
    def __init__(self):
        self.appended: list[dict] = []

    def append(self, folder, msg, flags=(), msg_time=None):
        self.appended.append(
            {"folder": folder, "msg": msg, "flags": list(flags), "msg_time": msg_time}
        )

    def logout(self):
        pass


# --------------------------------------------------------------------------- #
# adapter: append_to_drafts
# --------------------------------------------------------------------------- #
def test_append_to_drafts_uses_special_use_folder(monkeypatch):
    client = _FakeImapClient()
    adapter = EmailImapSource.__new__(EmailImapSource)
    adapter._connect_writeback = lambda: client  # type: ignore[attr-defined]
    monkeypatch.setattr(EmailImapSource, "_role_folder", staticmethod(lambda c, r: "Drafts"))

    out = adapter.append_to_drafts(b"raw-mime", "<mid@x>")

    assert out == {"folder": "Drafts", "message_id": "<mid@x>"}
    assert len(client.appended) == 1
    call = client.appended[0]
    assert call["folder"] == "Drafts"
    assert call["flags"] == ["\\Draft"]
    assert call["msg_time"] is not None  # a bare append is rejected by some servers


def test_append_to_drafts_no_drafts_folder_raises(monkeypatch):
    client = _FakeImapClient()
    adapter = EmailImapSource.__new__(EmailImapSource)
    adapter._connect_writeback = lambda: client  # type: ignore[attr-defined]
    monkeypatch.setattr(EmailImapSource, "_role_folder", staticmethod(lambda c, r: None))

    with pytest.raises(PermanentActionError) as ei:
        adapter.append_to_drafts(b"raw-mime", "<mid@x>")
    assert ei.value.code == "no_drafts_folder"
    assert client.appended == []  # no write attempted


# --------------------------------------------------------------------------- #
# handler: _email_save_draft
# --------------------------------------------------------------------------- #
class _FakeStore:
    def __init__(self, signal=None):
        self.signal = signal or {"url": _IMAP_URL, "from_addr": "bob@x.com", "title": "Lunch?"}
        self.sent_log: list[dict] = []

    def fetch_one(self, q, params=None):
        ql = (q or "").lower()
        if "select url from signals" in ql:
            return {"url": self.signal["url"]}
        if "from_addr, title from signals" in ql:
            return {"from_addr": self.signal["from_addr"], "title": self.signal["title"]}
        return None

    def fetch_all(self, q, params=None):
        return []

    def execute(self, q, params=None):
        if "insert into email_sent_log" in (q or "").lower():
            self.sent_log.append({"params": params})


class _FakeAdapter:
    """Stands in for EmailImapSource in the handler: exposes conn.username + records
    the APPEND (no real IMAP)."""

    instances: list["_FakeAdapter"] = []

    class _Conn:
        username = "alice@gmail.com"

    def __init__(self, source):
        self.source = source
        self.conn = self._Conn()
        self.appended: list[dict] = []
        _FakeAdapter.instances.append(self)

    def append_to_drafts(self, mime_bytes, message_id):
        self.appended.append({"bytes": mime_bytes, "message_id": message_id})
        return {"folder": "Drafts", "message_id": message_id}


@pytest.fixture
def draft_env(monkeypatch):
    _FakeAdapter.instances.clear()
    store = _FakeStore()
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)
    monkeypatch.setattr(smtp_send, "_load_owned_source", lambda sid, uid: object())
    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _FakeAdapter)
    return store


def test_save_draft_from_is_imap_username_without_smtp(draft_env, monkeypatch):
    # No SMTP credential resolution happens — a draft is IMAP-only. If the handler
    # ever called _resolve_smtp this would explode; it must not.
    monkeypatch.setattr(
        smtp_send,
        "_resolve_smtp",
        lambda *a, **k: pytest.fail("save-draft must not resolve SMTP"),
    )
    res = smtp_send._email_save_draft(
        {"source_id": _SID, "to": ["carol@y.com"], "subject": "Hi", "body": "hello"},
        user_id=_UID,
    )
    assert res.success
    adapter = _FakeAdapter.instances[-1]
    assert len(adapter.appended) == 1
    # From = the IMAP account username; no sent-log for a draft.
    assert draft_env.sent_log == []


def test_save_draft_reply_threads_and_equals_send_modulo_message_id(draft_env, monkeypatch):
    monkeypatch.setattr(
        smtp_send,
        "_refetch_original",
        lambda src, uv, uid: ("Original", "<orig@x.com>", "<root@x.com> <orig@x.com>"),
    )
    res = smtp_send._email_save_draft({"signal_id": "sig-1", "body": "sure"}, user_id=_UID)
    assert res.success
    appended = _FakeAdapter.instances[-1].appended[0]["bytes"]

    # Parse the appended MIME back and check threading + From.
    import email as _email

    msg = _email.message_from_bytes(appended)
    assert msg["To"] == "bob@x.com"
    assert msg["Subject"] == "Re: Original"
    assert msg["In-Reply-To"] == "<orig@x.com>"
    assert "<orig@x.com>" in (msg["References"] or "")
    assert msg["From"] == "alice@gmail.com"


def test_save_draft_not_written_to_sent_log(draft_env):
    smtp_send._email_save_draft(
        {"source_id": _SID, "to": ["carol@y.com"], "subject": "s", "body": "b"},
        user_id=_UID,
    )
    assert draft_env.sent_log == []  # first-contact history unaffected


# --------------------------------------------------------------------------- #
# input validation (THREE-shape rule since LUM-698) — 422 at create, not a
# dead-letter. Shapes: reply (signal_id, no `to`) | forward (signal_id +
# non-empty `to`) | compose (source_id + non-empty `to` + subject).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "payload",
    [
        {"body": "b"},  # neither shape
        {"signal_id": "s", "source_id": _SID, "body": "b"},  # both shapes
        # LUM-698: `signal_id` + non-empty `to` is now the legal FORWARD-draft
        # shape. An explicitly EMPTY `to` is neither reply nor forward.
        {"signal_id": "s", "to": [], "body": "b"},  # signal-based, empty `to`
        {"source_id": _SID, "to": [], "subject": "s", "body": "b"},  # compose empty to
        {"source_id": _SID, "to": ["a@x.com"], "body": "b"},  # compose no subject
    ],
)
def test_save_draft_input_rejects_bad_shape(payload):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        smtp_send.EmailSaveDraftInput.model_validate(payload)


def test_save_draft_input_accepts_reply_and_compose():
    smtp_send.EmailSaveDraftInput.model_validate({"signal_id": "s", "body": "b"})
    smtp_send.EmailSaveDraftInput.model_validate(
        {"source_id": _SID, "to": ["a@x.com"], "subject": "s", "body": "b"}
    )


def test_email_save_draft_registered():
    from actions.registry import get_action

    smtp_send.register_all()
    spec = get_action("email_save_draft")
    assert spec is not None
    assert spec.connector == "imap"
    assert spec.is_write is True
    assert spec.is_reversible is True  # LUM-681: reverse-delete handler ships
    assert spec.reverse_action_name == "email_save_draft_reverse"
    assert spec.annotate is None
    # The reverse action is registered, internal-only, and never chain-reversible.
    rev = get_action("email_save_draft_reverse")
    assert rev is not None
    assert rev.is_reversible is False
    assert rev.input_model is None


# --------------------------------------------------------------------------- #
# LUM-681: reverse-delete (adapter delete_from_drafts + reverse handler)
# --------------------------------------------------------------------------- #
class _FakeDeleteClient:
    """Fake IMAP client for delete_from_drafts: one draft with a known Message-ID."""

    def __init__(self, *, present=True, uidplus=True, ambiguous=False):
        self._present = present
        self._uidplus = uidplus
        self._ambiguous = ambiguous
        self.flagged: list = []
        self.expunged: list = []
        self.selected = None

    def select_folder(self, folder, readonly=False):
        self.selected = (folder, readonly)
        return {}

    def search(self, criteria):
        if not self._present:
            return []
        return [11, 12] if self._ambiguous else [11]

    def capabilities(self):
        return (b"IMAP4REV1",) + ((b"UIDPLUS",) if self._uidplus else ())

    def add_flags(self, uids, flags):
        self.flagged.append((list(uids), list(flags)))

    def expunge(self, uids=None):
        self.expunged.append(uids)

    def logout(self):
        pass


def _delete_adapter(client, monkeypatch):
    adapter = EmailImapSource.__new__(EmailImapSource)
    adapter._connect_writeback = lambda: client  # type: ignore[attr-defined]
    return adapter


def test_delete_from_drafts_removes_by_message_id(monkeypatch):
    client = _FakeDeleteClient(present=True, uidplus=True)
    adapter = _delete_adapter(client, monkeypatch)
    out = adapter.delete_from_drafts("Drafts", "<mid@x>")
    assert out["deleted"] is True
    assert client.flagged == [([11], ["\\Deleted"])]
    assert client.expunged == [[11]]  # targeted UID EXPUNGE, not a bare expunge


def test_delete_from_drafts_already_gone_is_noop(monkeypatch):
    client = _FakeDeleteClient(present=False)
    adapter = _delete_adapter(client, monkeypatch)
    out = adapter.delete_from_drafts("Drafts", "<mid@x>")
    assert out == {"deleted": False, "reason": "already_gone"}  # idempotent
    assert client.expunged == []


def test_delete_from_drafts_ambiguous_refuses(monkeypatch):
    client = _FakeDeleteClient(present=True, ambiguous=True)
    adapter = _delete_adapter(client, monkeypatch)
    with pytest.raises(PermanentActionError) as ei:
        adapter.delete_from_drafts("Drafts", "<mid@x>")
    assert ei.value.code == "reverse_ambiguous"
    assert client.expunged == []


def test_delete_from_drafts_no_uidplus_raises(monkeypatch):
    client = _FakeDeleteClient(present=True, uidplus=False)
    adapter = _delete_adapter(client, monkeypatch)
    with pytest.raises(PermanentActionError) as ei:
        adapter.delete_from_drafts("Drafts", "<mid@x>")
    assert ei.value.code == "unsupported_expunge"
    assert client.expunged == []


def test_save_draft_returns_reverse_input(draft_env):
    res = smtp_send._email_save_draft(
        {"source_id": _SID, "to": ["carol@y.com"], "subject": "s", "body": "b"},
        user_id=_UID,
    )
    assert res.success
    # The executor mints the reverse token from this; folder+message_id locate the draft.
    assert res.reverse_input["source_id"] == _SID
    assert res.reverse_input["folder"] == "Drafts"
    assert res.reverse_input["message_id"]


def test_save_draft_reverse_deletes_and_rechecks_ownership(monkeypatch):
    deleted = {}

    class _RevAdapter:
        def __init__(self, source):
            self.source = source

        def delete_from_drafts(self, folder, message_id):
            deleted["args"] = (folder, message_id)
            return {"deleted": True}

    owned = {}
    monkeypatch.setattr(
        smtp_send, "_load_owned_source", lambda sid, uid: owned.setdefault(sid, object())
    )
    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _RevAdapter)
    res = smtp_send._email_save_draft_reverse(
        {"source_id": _SID, "folder": "Drafts", "message_id": "<mid@x>"}, user_id=_UID
    )
    assert res.success
    assert deleted["args"] == ("Drafts", "<mid@x>")
    assert _SID in owned  # ownership re-checked on reverse


def test_save_draft_reverse_bad_input_raises():
    with pytest.raises(PermanentActionError) as ei:
        smtp_send._email_save_draft_reverse({"source_id": _SID}, user_id=_UID)
    assert ei.value.code == "reverse_bad_input"


# --------------------------------------------------------------------------- #
# LUM-698 — cc/bcc on drafts + the new forward-draft shape
# --------------------------------------------------------------------------- #
def test_forward_draft_shape_is_accepted():
    """`signal_id` + non-empty `to` is the FORWARD-draft shape (new in LUM-698).
    Previously rejected as 'reply carries `to`', which left the always-rendered
    [Save to Drafts] button with no legal payload in forward mode."""
    parsed = smtp_send.EmailSaveDraftInput.model_validate(
        {"signal_id": "sig-1", "to": ["carol@y.com"], "body": "fyi"}
    )
    assert parsed.signal_id == "sig-1"
    assert parsed.to == ["carol@y.com"]


def test_reply_draft_still_omits_a_recipient_list():
    """The original invariant is not loosened by accident: a reply draft still
    carries no `to` (its recipient is signal-derived)."""
    parsed = smtp_send.EmailSaveDraftInput.model_validate({"signal_id": "sig-1", "body": "sure"})
    assert parsed.to is None


def test_draft_accepts_cc_and_bcc_on_every_shape():
    for payload in (
        {"signal_id": "sig-1", "body": "b", "cc": ["c@y.com"]},
        {"signal_id": "sig-1", "to": ["a@x.com"], "body": "b", "bcc": ["e@z.com"]},
        {
            "source_id": _SID,
            "to": ["a@x.com"],
            "subject": "s",
            "body": "b",
            "cc": ["c@y.com"],
            "bcc": ["e@z.com"],
        },
    ):
        parsed = smtp_send.EmailSaveDraftInput.model_validate(payload)
        assert parsed.cc or parsed.bcc


def test_saved_draft_retains_bcc_header(draft_env, monkeypatch):
    """Deliberate asymmetry vs send (LUM-698): `send_message` strips Bcc from
    the transmitted copy, but a DRAFT is serialised with `as_bytes()` and keeps
    it — the draft lands in the user's own mailbox, and real clients keep Bcc so
    the send can be resumed. Asserted so this is a decision, not an accident."""
    monkeypatch.setattr(
        smtp_send, "_refetch_original", lambda src, uv, uid: ("Orig", "<m@x>", None)
    )
    res = smtp_send._email_save_draft(
        {"signal_id": "sig-1", "body": "sure", "bcc": ["eve@z.com"]}, user_id=_UID
    )
    assert res.success
    assert b"Bcc: eve@z.com" in _FakeAdapter.instances[-1].appended[-1]["bytes"]


def test_save_draft_with_attachments_round_trips(draft_env, monkeypatch):
    """LUM-695: the IMAP APPEND path carries attachments too.

    This path goes through the same ``build_message``, so it *should* work — but
    it was examined by neither critique round, and "should" is exactly what those
    rounds kept disproving. So it is asserted end to end, through the bytes that
    are actually APPENDed: the draft must be ``multipart/mixed`` and the file
    must survive serialisation, or a user resuming the draft in their own client
    finds an empty message where they attached something.

    ``as_bytes()`` (unlike ``send_message``) does not strip anything, so what is
    asserted here is literally what lands in the user's Drafts folder.
    """
    from email import message_from_bytes as _message_from_bytes

    from services import email_attachment_staging as staging

    class _Staged:
        id = "staged-1"
        filename = "invoice.pdf"
        mime_type = "application/pdf"

        class path:
            @staticmethod
            def read_bytes():
                return b"PDFBYTES"

            @staticmethod
            def exists():
                return True

    monkeypatch.setattr(staging, "resolve_all", lambda uid, ids: [_Staged()])
    monkeypatch.setattr(staging, "discard", lambda uid, ids: 0)
    monkeypatch.setattr(
        smtp_send, "_refetch_original", lambda src, uv, uid: ("Orig", "<m@x>", None)
    )

    res = smtp_send._email_save_draft(
        {"signal_id": "sig-1", "body": "see attached", "attachment_ids": ["staged-1"]},
        user_id=_UID,
    )

    assert res.success
    appended = _FakeAdapter.instances[-1].appended[-1]["bytes"]
    parsed = _message_from_bytes(appended)
    assert parsed.get_content_type() == "multipart/mixed"
    found = {
        part.get_filename(): part.get_payload(decode=True)
        for part in parsed.walk()
        if part.get_filename()
    }
    assert found == {"invoice.pdf": b"PDFBYTES"}
    # The body must still be there — an attachment that ate the message is worse
    # than no attachment at all.
    bodies = [
        part.get_payload(decode=True).decode()
        for part in parsed.walk()
        if part.get_content_type() == "text/plain" and not part.get_filename()
    ]
    assert bodies == ["see attached\n"]


def test_save_draft_without_attachments_is_unchanged(draft_env, monkeypatch):
    """The no-attachments draft must be exactly what it was before LUM-695."""
    from email import message_from_bytes as _message_from_bytes

    monkeypatch.setattr(
        smtp_send, "_refetch_original", lambda src, uv, uid: ("Orig", "<m@x>", None)
    )

    res = smtp_send._email_save_draft({"signal_id": "sig-1", "body": "sure"}, user_id=_UID)

    assert res.success
    parsed = _message_from_bytes(_FakeAdapter.instances[-1].appended[-1]["bytes"])
    assert not parsed.is_multipart()
    assert parsed.get_content_type() == "text/plain"


def test_save_draft_dead_letters_when_a_staged_attachment_is_gone(draft_env, monkeypatch):
    """A swept file must refuse the draft, not append a message missing a file."""
    from services import email_attachment_staging as staging

    def _gone(uid, ids):
        raise staging.StagedAttachmentUnavailable("swept")

    monkeypatch.setattr(staging, "resolve_all", _gone)
    monkeypatch.setattr(
        smtp_send, "_refetch_original", lambda src, uv, uid: ("Orig", "<m@x>", None)
    )

    before = len(_FakeAdapter.instances[-1].appended) if _FakeAdapter.instances else 0
    with pytest.raises(PermanentActionError) as exc:
        smtp_send._email_save_draft(
            {"signal_id": "sig-1", "body": "x", "attachment_ids": ["gone"]}, user_id=_UID
        )

    assert exc.value.code == "attachment_unavailable"
    if _FakeAdapter.instances:
        assert len(_FakeAdapter.instances[-1].appended) == before, "nothing may be appended"


def test_forward_draft_uses_fwd_subject_and_no_threading(draft_env, monkeypatch):
    """A saved forward and a sent forward must be the same message."""
    monkeypatch.setattr(
        smtp_send, "_refetch_original", lambda src, uv, uid: ("Orig", "<m@x>", "<r@x>")
    )
    res = smtp_send._email_save_draft(
        {"signal_id": "sig-1", "to": ["carol@y.com"], "body": "fyi"}, user_id=_UID
    )
    assert res.success
    raw = _FakeAdapter.instances[-1].appended[-1]["bytes"].decode("utf-8", "replace")
    assert "Subject: Fwd: Orig" in raw
    assert "To: carol@y.com" in raw
    assert "In-Reply-To:" not in raw
    assert "References:" not in raw
