# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Adapter-level attachment read path (LUM-695).

Three properties this file exists to pin, each one a defect a review round
found in the plan before any code was written:

* ``fetch_bodies_and_attachments`` produces text **and** metadata from ONE
  parse inside the adapter — ``process_signal`` never holds MIME bytes, so a
  design that parsed at the hook point could not have worked at all.
* The on-demand fetch does **not** assume INBOX. Organize (LUM-645) routinely
  moves classified mail into ``Lumogis/<label>``, and an INBOX-only fetch would
  410 on precisely the mail a tidy user has most of.
* The whole-MESSAGE size is prechecked via ``RFC822.SIZE`` before any
  ``BODY.PEEK[]``. The attachment cap is the wrong variable: a hostile 400 MB
  message carrying a 10 KB attachment passes it and still exhausts memory,
  because ``client.fetch`` buffers.
"""

from __future__ import annotations

from email.message import EmailMessage

import pytest
from adapters.email_imap_source import AttachmentFetchError
from adapters.email_imap_source import EmailImapSource
from models.signals import SourceConfig
from tests._fakes import FakeImapClient as _FakeClient

from services import email_credentials


@pytest.fixture(autouse=True)
def _conn(monkeypatch):
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


def _source(**kw) -> SourceConfig:
    base = dict(
        id="src-abc",
        name="Personal mail",
        source_type="imap",
        url="imap://imap.example.com:993/personal",
        category="",
        active=True,
        poll_interval=900,
        extraction_method="imap_full_ingest",
        css_selector_override=None,
        last_polled_at=None,
        last_signal_at=None,
        poll_cursor=None,
        user_id="u1",
    )
    base.update(kw)
    return SourceConfig(**base)


def _message(subject: str, *attachments: tuple[str, str, bytes]) -> bytes:
    msg = EmailMessage()
    msg["From"] = "sender@example.com"
    msg["Subject"] = subject
    msg["Message-ID"] = f"<{subject}@example.com>"
    msg.set_content(f"body of {subject}")
    for filename, mime, data in attachments:
        maintype, _, subtype = mime.partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return msg.as_bytes()


def _adapter(client: _FakeClient, source: SourceConfig | None = None) -> EmailImapSource:
    return EmailImapSource(source or _source(), client_factory=lambda _c: client)


# ---------------------------------------------------------------------------
# fetch_bodies_and_attachments — one parse, text + metadata
# ---------------------------------------------------------------------------


def test_metadata_is_produced_by_the_adapter_from_raw_bytes() -> None:
    """The read half's hook point: the ADAPTER holds the bytes, not process_signal."""
    raw = _message("m1", ("invoice.pdf", "application/pdf", b"%PDF fake"))
    client = _FakeClient({"INBOX": {7: raw}})

    out = _adapter(client).fetch_bodies_and_attachments([7])

    assert set(out) == {7}
    assert out[7].text == "body of m1"
    assert [a.filename for a in out[7].attachments] == ["invoice.pdf"]
    assert out[7].attachments[0].size_bytes == len(b"%PDF fake")
    assert len(client.body_peek_calls) == 1, "one fetch, one parse — no extra round trip"


def test_fetch_bodies_and_attachments_reports_the_whole_message_size() -> None:
    raw = _message("m1", ("a.pdf", "application/pdf", b"x" * 40))
    client = _FakeClient({"INBOX": {7: raw}})

    out = _adapter(client).fetch_bodies_and_attachments([7])

    assert out[7].message_size_bytes == len(raw)


def test_fetch_bodies_still_returns_plain_text_for_existing_callers() -> None:
    """``email_quote`` / ``email_reply_draft`` must keep their ``dict[int, str]``."""
    raw = _message("m1", ("a.pdf", "application/pdf", b"x"))
    client = _FakeClient({"INBOX": {7: raw}})

    out = _adapter(client).fetch_bodies([7])

    assert out == {7: "body of m1"}


def test_message_without_attachments_yields_an_empty_metadata_list() -> None:
    client = _FakeClient({"INBOX": {7: _message("m1")}})

    out = _adapter(client).fetch_bodies_and_attachments([7])

    assert out[7].attachments == []
    assert out[7].text == "body of m1"


# ---------------------------------------------------------------------------
# fetch_attachment_part — locating the message, and the two caps
# ---------------------------------------------------------------------------


def test_attachment_fetch_finds_a_message_organize_moved() -> None:
    """Organize moved it to ``Lumogis/Health``; the fetch must NOT 410."""
    raw = _message("moved", ("report.pdf", "application/pdf", b"REPORTBYTES"))
    client = _FakeClient({"INBOX": {}, "Lumogis/Health": {42: raw}})

    part = _adapter(client).fetch_attachment_part(
        uid=42, part_index=0, message_id="<moved@example.com>"
    )

    assert part.data == b"REPORTBYTES"
    assert part.filename == "report.pdf"


def test_attachment_fetch_uses_the_uid_directly_when_the_message_is_still_in_inbox() -> None:
    raw = _message("m1", ("a.pdf", "application/pdf", b"AAA"))
    client = _FakeClient({"INBOX": {7: raw}})

    part = _adapter(client).fetch_attachment_part(
        uid=7, part_index=0, message_id="<m1@example.com>"
    )

    assert part.data == b"AAA"


def test_410_only_after_the_message_id_search_comes_up_empty() -> None:
    """A genuinely deleted message still fails — but only after the search."""
    client = _FakeClient({"INBOX": {}, "Lumogis/Health": {}})

    with pytest.raises(AttachmentFetchError) as exc:
        _adapter(client).fetch_attachment_part(
            uid=42, part_index=0, message_id="<gone@example.com>"
        )

    assert exc.value.code == "message_gone"
    assert client.listed, "the Message-ID search must have swept the folder list"


def test_oversize_whole_message_refused_without_fetching() -> None:
    """The memory-exhaustion regression.

    A 400 MB message carrying a 10 KB attachment: refused from ``RFC822.SIZE``,
    and ``BODY.PEEK[]`` is NEVER called.
    """
    raw = _message("huge", ("small.pdf", "application/pdf", b"x" * 10_000))
    client = _FakeClient({"INBOX": {7: raw}}, sizes={7: 400 * 1024 * 1024})

    with pytest.raises(AttachmentFetchError) as exc:
        _adapter(client).fetch_attachment_part(uid=7, part_index=0, message_id="<huge@example.com>")

    assert exc.value.code == "message_too_large"
    assert client.body_peek_calls == [], "BODY.PEEK[] must never run for an oversize message"
    assert client.size_calls, "the RFC822.SIZE precheck must have run"


def test_missing_part_index_is_a_part_gone_error() -> None:
    raw = _message("m1", ("a.pdf", "application/pdf", b"AAA"))
    client = _FakeClient({"INBOX": {7: raw}})

    with pytest.raises(AttachmentFetchError) as exc:
        _adapter(client).fetch_attachment_part(uid=7, part_index=9, message_id="<m1@example.com>")

    assert exc.value.code == "part_gone"


def test_imap_connect_failure_is_distinct_from_message_gone() -> None:
    """503 vs 410: one is worth retrying and the other never will be."""

    def _boom(_conn):
        raise OSError("connection refused")

    adapter = EmailImapSource(_source(), client_factory=_boom)

    with pytest.raises(AttachmentFetchError) as exc:
        adapter.fetch_attachment_part(uid=7, part_index=0, message_id="<m@example.com>")

    assert exc.value.code == "imap_unreachable"


def test_attachment_fetch_prefers_the_folder_recorded_by_organize() -> None:
    """When organize logged where it moved the message, look there first."""
    raw = _message("moved", ("report.pdf", "application/pdf", b"R"))
    client = _FakeClient({"INBOX": {}, "Lumogis/Health": {42: raw}})

    part = _adapter(client).fetch_attachment_part(
        uid=42, part_index=0, message_id="<moved@example.com>", folder_hint="Lumogis/Health"
    )

    assert part.data == b"R"
    assert not client.listed, "a good hint should short-circuit the full folder sweep"


# ---------------------------------------------------------------------------
# Code-review findings — UIDVALIDITY identity, and transport-vs-gone
# ---------------------------------------------------------------------------


class _UidValidityClient(_FakeClient):
    """A mailbox whose UIDVALIDITY has been reassigned since ingest."""

    def __init__(self, folders, *, uidvalidity: int):
        super().__init__(folders)
        self._uidvalidity = uidvalidity

    def select_folder(self, folder, readonly=False):
        super().select_folder(folder, readonly=readonly)
        return {b"UIDVALIDITY": self._uidvalidity}


def test_stale_uidvalidity_never_serves_the_message_now_holding_that_uid() -> None:
    """Cross-message disclosure regression.

    A UIDVALIDITY reset reassigns UIDs, so the stored uid can now point at a
    DIFFERENT message. Serving its bytes under the recorded filename would hand
    the user someone else's mail with no error to notice — so the stored uid is
    only trusted when UIDVALIDITY still matches. The write paths already assert
    this (`_assert_uidvalidity`); the read path must too.
    """
    other = _message("other", ("someone-elses.pdf", "application/pdf", b"NOTYOURS"))
    client = _UidValidityClient({"INBOX": {42: other}}, uidvalidity=999)

    with pytest.raises(AttachmentFetchError) as exc:
        _adapter(client).fetch_attachment_part(
            uid=42, part_index=0, message_id="<mine@example.com>", uidvalidity=100
        )

    assert exc.value.code == "message_gone"
    assert client.body_peek_calls == [], "the wrong message must never be fetched"


def test_stale_uidvalidity_still_finds_the_right_message_by_message_id() -> None:
    """The Message-ID search is identity-safe, so it survives a UIDVALIDITY reset."""
    mine = _message("mine", ("report.pdf", "application/pdf", b"MINE"))
    client = _UidValidityClient({"INBOX": {7: mine}}, uidvalidity=999)

    part = _adapter(client).fetch_attachment_part(
        uid=42, part_index=0, message_id="<mine@example.com>", uidvalidity=100
    )

    assert part.data == b"MINE"


def test_matching_uidvalidity_still_takes_the_fast_uid_path() -> None:
    raw = _message("m1", ("a.pdf", "application/pdf", b"AAA"))
    client = _UidValidityClient({"INBOX": {7: raw}}, uidvalidity=100)

    part = _adapter(client).fetch_attachment_part(
        uid=7, part_index=0, message_id="<m1@example.com>", uidvalidity=100
    )

    assert part.data == b"AAA"


class _DroppingClient(_FakeClient):
    """Connects, then the connection drops on the first SELECT."""

    def select_folder(self, folder, readonly=False):
        raise OSError("connection reset by peer")


def test_a_transport_drop_while_locating_is_503_not_410() -> None:
    """A dropped connection must never be reported as "your message was deleted".

    410 says "this will never work"; a transient IMAP failure is precisely the
    retryable case. Collapsing the two tells the user their mail is gone when it
    is sitting in the mailbox.
    """
    client = _DroppingClient({"INBOX": {7: _message("m1")}})

    with pytest.raises(AttachmentFetchError) as exc:
        _adapter(client).fetch_attachment_part(uid=7, part_index=0, message_id="<m1@example.com>")

    assert exc.value.code == "imap_unreachable"


class _FetchFailsClient(_FakeClient):
    def fetch(self, uids, keys):
        if "BODY.PEEK[]" in keys:
            raise OSError("connection reset mid-fetch")
        return super().fetch(uids, keys)


def test_a_transport_drop_during_the_body_fetch_is_503_not_a_raw_500() -> None:
    raw = _message("m1", ("a.pdf", "application/pdf", b"AAA"))
    client = _FetchFailsClient({"INBOX": {7: raw}})

    with pytest.raises(AttachmentFetchError) as exc:
        _adapter(client).fetch_attachment_part(uid=7, part_index=0, message_id="<m1@example.com>")

    assert exc.value.code == "imap_unreachable"


def test_a_genuinely_missing_message_is_still_410_when_the_transport_is_fine() -> None:
    """The 503 widening must not swallow the real message-gone case."""
    client = _FakeClient({"INBOX": {}, "Archive": {}})

    with pytest.raises(AttachmentFetchError) as exc:
        _adapter(client).fetch_attachment_part(
            uid=42, part_index=0, message_id="<gone@example.com>"
        )

    assert exc.value.code == "message_gone"


class _DuplicateMessageIdClient(_FakeClient):
    """Two messages sharing one Message-ID — the header is attacker-authored."""

    def search(self, criteria):
        if criteria and criteria[0] == "HEADER":
            box = self._folders.get(self.selected or "", {})
            wanted = criteria[2]
            return [uid for uid, raw in box.items() if wanted.encode() in raw]
        return sorted(self._folders.get(self.selected or "", {}))


def test_ambiguous_message_id_refuses_rather_than_guessing() -> None:
    """``Message-ID`` is attacker-authored and not unique.

    A sender who knows an existing message's Message-ID can post a second
    message carrying the same one. Taking the first search hit would then serve
    THEIR bytes under the filename recorded for the original — a plausible file
    with the wrong contents, which is the exact failure this feature's design
    was built to avoid. The write paths already refuse to guess here
    (``_resolve_unique_uid`` → ``reverse_ambiguous``); so does the read path.
    """
    genuine = _message("dup", ("statement.pdf", "application/pdf", b"REAL"))
    forged = _message("dup", ("statement.pdf", "application/pdf", b"ATTACKER"))
    client = _DuplicateMessageIdClient({"INBOX": {7: genuine, 9: forged}})

    with pytest.raises(AttachmentFetchError) as exc:
        _adapter(client).fetch_attachment_part(
            uid=999, part_index=0, message_id="<dup@example.com>"
        )

    assert exc.value.code == "message_ambiguous"
    assert client.body_peek_calls == [], "nothing may be fetched when identity is ambiguous"


def test_a_single_message_id_match_still_resolves() -> None:
    """The refusal must not break the ordinary organized-mail case."""
    raw = _message("moved", ("report.pdf", "application/pdf", b"REPORT"))
    client = _DuplicateMessageIdClient({"INBOX": {}, "Lumogis/Health": {42: raw}})

    part = _adapter(client).fetch_attachment_part(
        uid=42, part_index=0, message_id="<moved@example.com>"
    )

    assert part.data == b"REPORT"


# ---------------------------------------------------------------------------
# LUM-712 — batch fetch for forward-with-attachments
# ---------------------------------------------------------------------------


def test_fetch_attachment_parts_returns_every_requested_index() -> None:
    raw = _message(
        "m1",
        ("a.pdf", "application/pdf", b"AAA"),
        ("b.png", "image/png", b"BBB"),
        ("c.txt", "text/plain", b"CCC"),
    )
    client = _FakeClient({"INBOX": {7: raw}})

    parts = _adapter(client).fetch_attachment_parts(
        uid=7, part_indexes=[0, 2], message_id="<m1@example.com>"
    )

    assert [p.filename for p in parts] == ["a.pdf", "c.txt"]
    assert [p.data for p in parts] == [b"AAA", b"CCC"]


def test_fetch_attachment_parts_fetches_the_message_once_for_many_parts() -> None:
    """Forwarding three attachments must not mean three whole-message fetches."""
    raw = _message(
        "m1",
        ("a.pdf", "application/pdf", b"AAA"),
        ("b.png", "image/png", b"BBB"),
        ("c.txt", "text/plain", b"CCC"),
    )
    client = _FakeClient({"INBOX": {7: raw}})

    _adapter(client).fetch_attachment_parts(
        uid=7, part_indexes=[0, 1, 2], message_id="<m1@example.com>"
    )

    assert len(client.body_peek_calls) == 1, "one message, one fetch, one parse"


def test_fetch_attachment_parts_preserves_the_requested_order() -> None:
    raw = _message("m1", ("a.pdf", "application/pdf", b"AAA"), ("b.png", "image/png", b"BBB"))
    client = _FakeClient({"INBOX": {7: raw}})

    parts = _adapter(client).fetch_attachment_parts(
        uid=7, part_indexes=[1, 0], message_id="<m1@example.com>"
    )

    assert [p.filename for p in parts] == ["b.png", "a.pdf"]


def test_fetch_attachment_parts_raises_part_gone_for_an_unknown_index() -> None:
    """All-or-nothing: a forward missing one file is worse than one that refuses."""
    raw = _message("m1", ("a.pdf", "application/pdf", b"AAA"))
    client = _FakeClient({"INBOX": {7: raw}})

    with pytest.raises(AttachmentFetchError) as exc:
        _adapter(client).fetch_attachment_parts(
            uid=7, part_indexes=[0, 9], message_id="<m1@example.com>"
        )

    assert exc.value.code == "part_gone"


def test_fetch_attachment_parts_with_no_indexes_never_opens_a_connection() -> None:
    def _boom(_conn):
        raise AssertionError("no IMAP connection may be opened for an empty request")

    adapter = EmailImapSource(_source(), client_factory=_boom)

    assert adapter.fetch_attachment_parts(uid=7, part_indexes=[], message_id="<m@x>") == []


def test_the_batch_fetch_honours_the_whole_message_cap() -> None:
    raw = _message("huge", ("a.pdf", "application/pdf", b"x" * 10))
    client = _FakeClient({"INBOX": {7: raw}}, sizes={7: 400 * 1024 * 1024})

    with pytest.raises(AttachmentFetchError) as exc:
        _adapter(client).fetch_attachment_parts(
            uid=7, part_indexes=[0], message_id="<huge@example.com>"
        )

    assert exc.value.code == "message_too_large"
    assert client.body_peek_calls == []
