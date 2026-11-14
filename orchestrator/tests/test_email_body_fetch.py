# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Adapter-level body read path (LUM-810).

`fetch_body_bounded` is the body sibling of `fetch_attachment_parts` — same
locate-then-size-check-then-fetch shape, reusing `_locate_message` unchanged.
The properties pinned here are the ones a plan review round found missing:

* **Size is checked before any body is downloaded**, and the check must still
  hold when the server *withholds* `RFC822.SIZE`. `_message_size` returns
  `None` then, and the shipped precheck degrades open — the attachment path
  survives that on its per-attachment cap, a body read has no such backstop.
* **No expected failure escapes as an exception.** Every `AttachmentFetchError`
  the shipped locate/fetch machinery raises maps to a `reason`, so the route
  can answer 200 with a reason rather than a 5xx on hostile or absent mail.
"""

from __future__ import annotations

from email.message import EmailMessage

import pytest
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


def _message(subject: str, body: str, *, html: str | None = None) -> bytes:
    msg = EmailMessage()
    msg["From"] = "sender@example.com"
    msg["Subject"] = subject
    msg["Message-ID"] = f"<{subject}@example.com>"
    msg.set_content(body)
    if html is not None:
        msg.add_alternative(html, subtype="html")
    return msg.as_bytes()


def _adapter(client: _FakeClient, source: SourceConfig | None = None) -> EmailImapSource:
    return EmailImapSource(source or _source(), client_factory=lambda _c: client)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_fetch_body_when_message_present_then_returns_extracted_text():
    raw = _message("m1", "The quick brown fox.")
    client = _FakeClient({"INBOX": {7: raw}})

    out = _adapter(client).fetch_body_bounded(uid=7, message_id="<m1@example.com>", uidvalidity=100)

    assert out.reason == "ok"
    assert "The quick brown fox." in out.text


def test_fetch_body_when_html_only_then_returns_text_not_markup():
    """The read view's whole point: HTML-only mail was previously blank."""
    msg = EmailMessage()
    msg["From"] = "sender@example.com"
    msg["Message-ID"] = "<h1@example.com>"
    msg.set_content("<p>Your order shipped.</p>", subtype="html")
    client = _FakeClient({"INBOX": {7: msg.as_bytes()}})

    out = _adapter(client).fetch_body_bounded(uid=7, message_id="<h1@example.com>", uidvalidity=100)

    assert out.reason == "ok"
    assert "Your order shipped." in out.text
    assert "<p>" not in out.text


def test_fetch_body_when_message_in_another_folder_then_located_by_message_id():
    """Organize (LUM-645) moves classified mail; INBOX-only would report gone."""
    raw = _message("filed", "Filed away but readable.")
    client = _FakeClient({"INBOX": {}, "Lumogis/Receipts": {12: raw}})

    out = _adapter(client).fetch_body_bounded(
        uid=99, message_id="<filed@example.com>", uidvalidity=100
    )

    assert out.reason == "ok"
    assert "Filed away but readable." in out.text


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


def test_fetch_body_when_oversize_and_text_only_unavailable_then_refused_without_downloading():
    """Mirrors `test_oversize_whole_message_refused_without_fetching`.

    VERIFY-PLAN: `withhold_bodystructure` added when LUM-814 landed. Oversize no
    longer means refuse on its own — the text-only path gets a go first, and for
    a message whose text is small it now succeeds, which is the entire point of
    that change. This test owns the *refusal* leg, so it has to be the case
    where text-only genuinely cannot run. The success leg has its own test.
    """
    raw = _message("big", "x")
    client = _FakeClient(
        {"INBOX": {7: raw}}, sizes={7: 500 * 1024 * 1024}, withhold_bodystructure=True
    )

    out = _adapter(client).fetch_body_bounded(
        uid=7, message_id="<big@example.com>", uidvalidity=100
    )

    assert out.reason == "too_large"
    assert out.text == ""
    assert client.size_calls
    assert client.body_peek_calls == []


def test_fetch_body_when_server_withholds_size_then_still_refused_after_fetch():
    """The precheck degrades OPEN — `_message_size` returns None.

    A body read has no per-object backstop, so the post-fetch length check is
    the only thing standing between hostile mail and the parser.
    """
    big = b"From: a@example.com\r\nMessage-ID: <big@example.com>\r\n\r\n" + (b"x" * 2_000_000)
    client = _FakeClient({"INBOX": {7: big}}, withhold_size=True)

    out = _adapter(client).fetch_body_bounded(
        uid=7, message_id="<big@example.com>", uidvalidity=100
    )

    assert out.reason == "too_large"
    assert out.text == ""


# ---------------------------------------------------------------------------
# Failure reasons — none of these may escape as an exception
# ---------------------------------------------------------------------------


def test_fetch_body_when_the_folder_sweep_stalls_then_it_gives_up_as_unreachable(
    monkeypatch,
):
    """The socket timeout bounds ONE operation; the sweep does many.

    A mailbox with forty folders could otherwise hold a threadpool worker for
    forty times the per-op budget on a synchronous, user-facing read. Giving up
    must read as `mailbox_unreachable` — we never finished looking, so claiming
    the message is gone would be a lie.
    """
    import adapters.email_imap_source as mod

    folders = {f"F{i}": {} for i in range(40)}
    folders["INBOX"] = {}
    client = _FakeClient(folders)

    # Time leaps past the deadline once the sweep is under way.
    ticks = iter([0.0, 0.0, 0.0] + [10_000.0] * 200)
    monkeypatch.setattr(mod.time, "monotonic", lambda: next(ticks, 10_000.0))

    out = _adapter(client).fetch_body_bounded(uid=7, message_id="<x@example.com>", uidvalidity=100)

    assert out.reason == "mailbox_unreachable"
    assert out.text == ""


def test_fetch_body_when_message_absent_then_not_found():
    client = _FakeClient({"INBOX": {}})

    out = _adapter(client).fetch_body_bounded(
        uid=7, message_id="<gone@example.com>", uidvalidity=100
    )

    assert out.reason == "not_found"
    assert out.text == ""


def test_fetch_body_when_message_id_is_ambiguous_then_reason_not_an_exception():
    """`_locate_message` RAISES here; unmapped that is a 500 on sender-authored
    input, since nothing enforces Message-ID uniqueness."""
    dupe = _message("dupe", "first")
    other = _message("dupe", "second")
    client = _FakeClient({"INBOX": {1: dupe, 2: other}})

    out = _adapter(client).fetch_body_bounded(
        uid=99, message_id="<dupe@example.com>", uidvalidity=0
    )

    assert out.reason == "ambiguous"
    assert out.text == ""


def test_fetch_body_when_mailbox_unreachable_then_degrades_not_raises():
    def _boom(_conn):
        raise RuntimeError("connection refused")

    adapter = EmailImapSource(_source(), client_factory=_boom)

    out = adapter.fetch_body_bounded(uid=7, message_id="<m@example.com>", uidvalidity=100)

    assert out.reason == "mailbox_unreachable"
    assert out.text == ""


def test_fetch_body_when_message_has_no_text_then_no_body():
    msg = EmailMessage()
    msg["From"] = "sender@example.com"
    msg["Message-ID"] = "<empty@example.com>"
    msg.set_content(b"\x00\x01", maintype="application", subtype="octet-stream")
    client = _FakeClient({"INBOX": {7: msg.as_bytes()}})

    out = _adapter(client).fetch_body_bounded(
        uid=7, message_id="<empty@example.com>", uidvalidity=100
    )

    assert out.reason == "no_body"
    assert out.text == ""


# ---------------------------------------------------------------------------
# Text-only fetch — an attachment must not decide whether text is readable
# ---------------------------------------------------------------------------


def _with_attachment(body_html: str, attachment_bytes: int) -> bytes:
    msg = EmailMessage()
    msg["From"] = "mum@example.com"
    msg["Subject"] = "photo"
    msg["Message-ID"] = "<photo@example.com>"
    msg.set_content(body_html, subtype="html")
    msg.add_attachment(
        b"\xff\xd8" + b"P" * attachment_bytes,
        maintype="image",
        subtype="jpeg",
        filename="sunday.jpg",
    )
    return msg.as_bytes()


def test_fetch_body_when_a_big_attachment_dwarfs_the_text_then_text_is_still_returned():
    """LUM-814: the readability of the TEXT must not be decided by the size of
    things that are not the text.

    Before this, `BODY.PEEK[]` pulled the whole message and the whole-message
    cap refused a one-line email carrying a photo.
    """
    raw = _with_attachment("<p>Here's the photo from Sunday.</p>", 2_000_000)
    client = _FakeClient({"INBOX": {7: raw}})

    out = _adapter(client).fetch_body_bounded(
        uid=7, message_id="<photo@example.com>", uidvalidity=100
    )

    assert out.reason == "ok"
    assert "Here's the photo from Sunday." in out.text
    # The attachment's bytes were never transferred.
    assert client.body_peek_calls == [], "the whole message must not be fetched"
    assert any(".MIME]" in k for k in client.part_fetch_keys)


def test_fetch_body_when_the_server_withholds_bodystructure_then_it_falls_back():
    """Every surprise in the text-only path means "as before", never "wrong".

    Real servers have BODYSTRUCTURE quirks no fake covers, so the fallback is
    the feature — an unreadable-but-correct refusal beats confident nonsense.
    """
    raw = _with_attachment("<p>Here's the photo.</p>", 2_000_000)
    client = _FakeClient({"INBOX": {7: raw}}, withhold_bodystructure=True)

    out = _adapter(client).fetch_body_bounded(
        uid=7, message_id="<photo@example.com>", uidvalidity=100
    )

    assert out.reason == "too_large"
    assert out.text == ""


def test_fetch_body_when_the_TEXT_itself_is_oversized_then_still_too_large():
    """The cap is not removed, only aimed correctly. A genuinely huge body is
    still refused — that is a real limit, not an attachment's fault."""
    msg = EmailMessage()
    msg["From"] = "a@example.com"
    msg["Message-ID"] = "<huge@example.com>"
    msg.set_content("<p>" + ("x" * 2_000_000) + "</p>", subtype="html")
    client = _FakeClient({"INBOX": {7: msg.as_bytes()}})

    out = _adapter(client).fetch_body_bounded(
        uid=7, message_id="<huge@example.com>", uidvalidity=100
    )

    assert out.reason == "too_large"
    assert out.text == ""


def test_fetch_body_when_under_the_cap_then_the_whole_message_path_is_used():
    """Small messages keep the single round trip — the text-only path costs an
    extra BODYSTRUCTURE fetch and only earns it when the whole is oversized."""
    raw = _message("small", "Just a short note.")
    client = _FakeClient({"INBOX": {7: raw}})

    out = _adapter(client).fetch_body_bounded(
        uid=7, message_id="<small@example.com>", uidvalidity=100
    )

    assert out.reason == "ok"
    assert client.body_peek_calls == [[7]]
    assert client.part_fetch_keys == []


# ---------------------------------------------------------------------------
# Inline images (LUM-713) — one session, both paths, same answer
# ---------------------------------------------------------------------------

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _with_inline_image(*, filler: int = 0) -> bytes:
    """A message with a cid: logo, and optionally an attachment to make it big."""
    msg = EmailMessage()
    msg["From"] = "school@example.com"
    msg["Subject"] = "sports day"
    msg["Message-ID"] = "<inline@example.com>"
    msg.set_content("<p>Sports day is Thursday.</p>", subtype="html")
    img = EmailMessage()
    img.set_content(_PNG, maintype="image", subtype="png")
    img.add_header("Content-ID", "<logo1>")
    img.add_header("Content-Disposition", "inline")
    msg.make_mixed()
    msg.attach(img)
    if filler:
        msg.add_attachment(b"P" * filler, maintype="application", subtype="pdf", filename="f.pdf")
    return msg.as_bytes()


def test_inline_images_are_returned_on_the_normal_path_with_no_extra_fetch():
    raw = _with_inline_image()
    client = _FakeClient({"INBOX": {7: raw}})

    out = _adapter(client).fetch_body_bounded(
        uid=7, message_id="<inline@example.com>", uidvalidity=100
    )

    assert out.reason == "ok"
    assert [i.mime_type for i in out.inline_images] == ["image/png"]
    # The bytes were already in hand — no named-part fetch was needed.
    assert client.part_fetch_keys == []


def test_inline_images_are_found_on_the_oversized_path_too():
    """The same message, too big to fetch whole. Same answer, one session."""
    raw = _with_inline_image(filler=2_000_000)
    client = _FakeClient({"INBOX": {7: raw}})

    out = _adapter(client).fetch_body_bounded(
        uid=7, message_id="<inline@example.com>", uidvalidity=100
    )

    assert out.reason == "ok"
    assert [i.mime_type for i in out.inline_images] == ["image/png"]
    # The attachment's 2 MB was never transferred.
    assert client.body_peek_calls == []


def test_the_two_paths_agree_on_the_same_message():
    """The property that matters after the numbering divergence review finding:
    normal and oversized must return the SAME images for the same message."""
    raw = _with_inline_image()
    normal = _adapter(_FakeClient({"INBOX": {7: raw}})).fetch_body_bounded(
        uid=7, message_id="<inline@example.com>", uidvalidity=100
    )
    big = _adapter(
        _FakeClient({"INBOX": {7: raw}}, sizes={7: 500 * 1024 * 1024})
    ).fetch_body_bounded(uid=7, message_id="<inline@example.com>", uidvalidity=100)

    assert [(i.mime_type, i.size_bytes) for i in normal.inline_images] == [
        (i.mime_type, i.size_bytes) for i in big.inline_images
    ]
    assert normal.inline_images_truncated == big.inline_images_truncated


def _inline_part(cid: str, data: bytes, *, disposition: str = "inline") -> EmailMessage:
    part = EmailMessage()
    part.set_content(data, maintype="image", subtype="png")
    part.add_header("Content-ID", cid)
    if disposition == "inline":
        part.add_header("Content-Disposition", "inline")
    else:
        part.add_header("Content-Disposition", "attachment", filename=f"{cid.strip('<>')}.png")
    return part


def _shaped(parts: list[EmailMessage]) -> bytes:
    msg = EmailMessage()
    msg["From"] = "sender@example.com"
    msg["Subject"] = "shaped"
    msg["Message-ID"] = "<shaped@example.com>"
    msg.set_content("Some text.")
    msg.make_mixed()
    for part in parts:
        msg.attach(part)
    return msg.as_bytes()


_MID_SIZED = _PNG[:8] + b"\x00" * (400 * 1024 - 8)


@pytest.mark.parametrize(
    "name,parts",
    [
        ("one small image", [_inline_part("<a>", _PNG)]),
        # 400 KiB decoded is UNDER the 512 KiB cap but ~540 KiB once base64'd.
        # Comparing a BODYSTRUCTURE size (encoded, RFC 3501) against the decoded
        # cap dropped exactly this band on the oversized path only.
        ("an image in the base64 inflation band", [_inline_part("<a>", _MID_SIZED)]),
        # A photo the sender marked `attachment` AND gave a Content-ID: it is in
        # the ATTACHMENT list, so neither path may show it — and neither may
        # spend a strip slot on it and then call the difference truncation.
        (
            "attachment-disposition images ahead of real inline ones",
            [_inline_part(f"<x{n}>", _PNG, disposition="attachment") for n in range(3)]
            + [_inline_part(f"<i{n}>", _PNG) for n in range(6)],
        ),
        ("a part that will not sniff", [_inline_part("<a>", b"<svg/>" + b"x" * 64)]),
        ("nothing inline at all", [_inline_part("<a>", _PNG, disposition="attachment")]),
    ],
)
def test_the_two_paths_agree_on_every_shape_that_has_ever_diverged(name, parts):
    """Table-driven, because the single-small-PNG version of this test passed
    while three separate divergences sat underneath it (review round 2).

    Both paths answer from the same message; only the size the server reports
    differs, which is what sends one down the whole-message read and the other
    down the named-part read.
    """
    raw = _shaped(parts)

    normal = _adapter(_FakeClient({"INBOX": {7: raw}})).fetch_body_bounded(
        uid=7, message_id="<shaped@example.com>", uidvalidity=100
    )
    big = _adapter(
        _FakeClient({"INBOX": {7: raw}}, sizes={7: 500 * 1024 * 1024})
    ).fetch_body_bounded(uid=7, message_id="<shaped@example.com>", uidvalidity=100)

    assert [(i.content_id, i.mime_type, i.size_bytes) for i in normal.inline_images] == [
        (i.content_id, i.mime_type, i.size_bytes) for i in big.inline_images
    ], name
    assert normal.inline_images_truncated == big.inline_images_truncated, name


def test_an_attachment_disposition_image_is_never_fetched_for_the_strip():
    """It belongs to the attachment list, and pulling its bytes here would
    spend the strip's transfer budget on something the collector then drops."""
    raw = _shaped(
        [_inline_part("<big>", _MID_SIZED, disposition="attachment"), _inline_part("<ok>", _PNG)]
    )
    client = _FakeClient({"INBOX": {7: raw}}, sizes={7: 500 * 1024 * 1024})

    out = _adapter(client).fetch_body_bounded(
        uid=7, message_id="<shaped@example.com>", uidvalidity=100
    )

    assert [i.content_id for i in out.inline_images] == ["<ok>"]
    assert not any("<big>" in str(k) for k in client.part_fetch_keys)


def test_the_oversized_path_costs_one_bodystructure_not_two():
    """The text walk and the image walk describe the same tree. Asking the
    server twice spent a round trip on an answer already in hand, while the
    docstring claimed the walk was reused."""
    raw = _shaped([_inline_part("<a>", _PNG)])

    class _Counting(_FakeClient):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.bodystructure_calls = 0

        def fetch(self, uids, keys):
            if any("BODYSTRUCTURE" in str(k) for k in keys):
                self.bodystructure_calls += 1
            return super().fetch(uids, keys)

    client = _Counting({"INBOX": {7: raw}}, sizes={7: 500 * 1024 * 1024})
    _adapter(client).fetch_body_bounded(uid=7, message_id="<shaped@example.com>", uidvalidity=100)

    assert client.bodystructure_calls == 1


def test_the_transfer_budget_is_spent_before_the_bytes_move_not_after():
    """The aggregate cap existed only in the collector, so six near-cap images
    pulled ~3 MB to then discard ~1 MB of it — on the path whose whole purpose
    is not moving bytes the reader will never see."""
    from services.email_inline_images import MAX_INLINE_TRANSFER_BYTES

    each = _PNG[:8] + b"\x00" * (500 * 1024 - 8)
    raw = _shaped([_inline_part(f"<{n}>", each) for n in range(6)])
    client = _FakeClient({"INBOX": {7: raw}}, sizes={7: 500 * 1024 * 1024})

    out = _adapter(client).fetch_body_bounded(
        uid=7, message_id="<shaped@example.com>", uidvalidity=100
    )

    requested = sum(1 for k in client.part_fetch_keys if ".MIME]" not in str(k))
    assert requested * len(each) <= MAX_INLINE_TRANSFER_BYTES
    assert out.inline_images_truncated is True


def test_the_count_cap_is_reported_on_both_paths():
    """The oversized path caps a step earlier — before fetching — so it has to
    report the bit itself. The collector never sees what was not fetched."""
    from services.email_inline_images import MAX_INLINE_IMAGES

    msg = EmailMessage()
    msg["From"] = "album@example.com"
    msg["Subject"] = "holiday"
    msg["Message-ID"] = "<many@example.com>"
    msg.set_content("photos")
    msg.make_mixed()
    for n in range(MAX_INLINE_IMAGES + 2):
        img = EmailMessage()
        img.set_content(_PNG, maintype="image", subtype="png")
        img.add_header("Content-ID", f"<i{n}>")
        img.add_header("Content-Disposition", "inline")
        msg.attach(img)
    raw = msg.as_bytes()

    normal = _adapter(_FakeClient({"INBOX": {7: raw}})).fetch_body_bounded(
        uid=7, message_id="<many@example.com>", uidvalidity=100
    )
    big = _adapter(
        _FakeClient({"INBOX": {7: raw}}, sizes={7: 500 * 1024 * 1024})
    ).fetch_body_bounded(uid=7, message_id="<many@example.com>", uidvalidity=100)

    assert len(normal.inline_images) == MAX_INLINE_IMAGES
    assert len(big.inline_images) == MAX_INLINE_IMAGES
    assert normal.inline_images_truncated is True
    assert big.inline_images_truncated is True


def test_a_normal_message_is_not_reported_as_truncated():
    out = _adapter(_FakeClient({"INBOX": {7: _with_inline_image()}})).fetch_body_bounded(
        uid=7, message_id="<inline@example.com>", uidvalidity=100
    )

    assert out.inline_images_truncated is False


def test_a_message_with_no_inline_images_yields_an_empty_strip():
    client = _FakeClient({"INBOX": {7: _message("plain", "just text")}})

    out = _adapter(client).fetch_body_bounded(
        uid=7, message_id="<plain@example.com>", uidvalidity=100
    )

    assert out.inline_images == ()


def test_an_unusable_bodystructure_costs_the_strip_not_the_message():
    """A strip is never the reason a message fails to open."""
    raw = _with_inline_image(filler=2_000_000)
    client = _FakeClient({"INBOX": {7: raw}}, withhold_bodystructure=True)

    out = _adapter(client).fetch_body_bounded(
        uid=7, message_id="<inline@example.com>", uidvalidity=100
    )

    assert out.reason == "too_large"
    assert out.inline_images == ()
