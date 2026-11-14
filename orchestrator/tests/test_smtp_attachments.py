# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``build_message`` grows a ``multipart/mixed`` path (LUM-695).

``build_message`` is the single MIME builder for reply / compose / forward /
save-draft, so anything added to it lands in all four. That is the point, and it
is also why the no-attachments case needs a regression test with teeth.

The obvious version of that test — "byte-identical to before" — **cannot pass**:
``build_message`` calls ``make_msgid(domain=…)``, which embeds a timestamp, a
pid and a random token, so two builds of *unchanged* code are never byte-equal.
The assertion here is therefore **structural**: freeze ``make_msgid`` to a fixed
value, serialise, and compare against a golden captured before the change.
"""

from __future__ import annotations

from email import message_from_bytes

import pytest
from actions.errors import PermanentActionError
from adapters import email_smtp_sender as smtp_mod
from adapters.email_smtp_sender import AttachmentPart
from adapters.email_smtp_sender import SmtpSender

_FROZEN_MSGID = "<frozen-message-id@example.com>"
# Already in the canonical serialised form. The email policy re-writes a Date
# header on output — it corrects the weekday and renders `GMT` as `+0000` — so a
# frozen value in `formatdate(usegmt=True)`'s own style would not round-trip and
# the golden would be comparing against a string the library never emits.
_FROZEN_DATE = "Thu, 01 Jan 2026 00:00:00 +0000"

# Captured from `build_message` BEFORE the attachments parameter existed, with
# make_msgid frozen to _FROZEN_MSGID. Any change to the no-attachments output —
# a header added, an order changed, a body re-encoded — fails this.
#
# Updated for LUM-65 P1 #9: `Date` is now emitted. RFC 5322 §3.6 makes it
# mandatory and `smtplib.send_message` does not add one, so every message from
# this sender went out without it. `formatdate` is frozen alongside `make_msgid`
# for the same reason — it embeds the wall clock, so an unfrozen value makes
# byte-equality unachievable and the guard meaningless.
_GOLDEN_NO_ATTACHMENTS = (
    "From: me@example.com\n"
    "To: you@example.com\n"
    "Cc: cc@example.com\n"
    "Subject: Hello\n"
    f"Date: {_FROZEN_DATE}\n"
    "Message-ID: <frozen-message-id@example.com>\n"
    'Content-Type: text/plain; charset="utf-8"\n'
    "Content-Transfer-Encoding: 7bit\n"
    "MIME-Version: 1.0\n"
    "\n"
    "Body line one.\n"
)


@pytest.fixture
def frozen_msgid(monkeypatch):
    monkeypatch.setattr(smtp_mod, "make_msgid", lambda domain=None: _FROZEN_MSGID)
    monkeypatch.setattr(smtp_mod, "formatdate", lambda *a, **kw: _FROZEN_DATE)


def _build(**kw):
    base = dict(
        from_addr="me@example.com",
        to=["you@example.com"],
        subject="Hello",
        body="Body line one.",
    )
    base.update(kw)
    return SmtpSender.build_message(**base)


# ---------------------------------------------------------------------------
# The regression the other four send paths depend on
# ---------------------------------------------------------------------------


def test_build_message_without_attachments_matches_golden(frozen_msgid) -> None:
    msg = _build(cc=["cc@example.com"])

    assert msg.as_string() == _GOLDEN_NO_ATTACHMENTS


def test_build_message_without_attachments_is_not_multipart(frozen_msgid) -> None:
    assert not _build().is_multipart()


def test_empty_attachment_iterable_is_the_same_as_omitting_it(frozen_msgid) -> None:
    assert _build(cc=["cc@example.com"], attachments=()).as_string() == _GOLDEN_NO_ATTACHMENTS


# ---------------------------------------------------------------------------
# The multipart/mixed path
# ---------------------------------------------------------------------------


def test_attachments_produce_a_multipart_mixed_message(frozen_msgid) -> None:
    msg = _build(
        attachments=[
            AttachmentPart(filename="invoice.pdf", mime_type="application/pdf", data=b"PDF")
        ]
    )

    assert msg.get_content_type() == "multipart/mixed"
    assert msg["Subject"] == "Hello"
    assert msg["Message-ID"] == _FROZEN_MSGID


def test_the_body_survives_alongside_the_attachments(frozen_msgid) -> None:
    msg = _build(
        body="Please see attached.",
        attachments=[AttachmentPart(filename="a.pdf", mime_type="application/pdf", data=b"A")],
    )

    bodies = [
        p.get_payload(decode=True).decode()
        for p in msg.walk()
        if p.get_content_type() == "text/plain" and not p.get_filename()
    ]
    assert bodies == ["Please see attached.\n"]


def test_each_attachment_carries_its_filename_mime_and_bytes(frozen_msgid) -> None:
    msg = _build(
        attachments=[
            AttachmentPart(filename="invoice.pdf", mime_type="application/pdf", data=b"PDFBYTES"),
            AttachmentPart(filename="photo.png", mime_type="image/png", data=b"PNGBYTES"),
        ]
    )

    # Round-trip through serialisation — what the recipient actually receives.
    parsed = message_from_bytes(msg.as_bytes())
    found = {
        p.get_filename(): (p.get_content_type(), p.get_payload(decode=True))
        for p in parsed.walk()
        if p.get_filename()
    }
    assert found == {
        "invoice.pdf": ("application/pdf", b"PDFBYTES"),
        "photo.png": ("image/png", b"PNGBYTES"),
    }


def test_an_unparseable_mime_type_degrades_to_octet_stream(frozen_msgid) -> None:
    msg = _build(attachments=[AttachmentPart(filename="x.bin", mime_type="not-a-mime", data=b"X")])

    parsed = message_from_bytes(msg.as_bytes())
    types = [p.get_content_type() for p in parsed.walk() if p.get_filename()]
    assert types == ["application/octet-stream"]


def test_attachment_filenames_are_sanitised_into_the_mime_part(frozen_msgid) -> None:
    """A traversal-shaped name must not reach the recipient's filesystem either."""
    msg = _build(
        attachments=[
            AttachmentPart(filename="../../etc/passwd", mime_type="application/pdf", data=b"X")
        ]
    )

    parsed = message_from_bytes(msg.as_bytes())
    names = [p.get_filename() for p in parsed.walk() if p.get_filename()]
    assert names and all("/" not in n and ".." not in n for n in names)


def test_bcc_is_still_a_header_on_the_multipart_path(frozen_msgid) -> None:
    """The BCC contract must not be lost when the structure changes.

    ``send_message`` builds its envelope from To+Cc+Bcc and strips Bcc from the
    transmitted copy; ``email_save_draft`` deliberately keeps it via
    ``as_bytes()``. Both depend on the header actually being set.
    """
    msg = _build(
        bcc=["hidden@example.com"],
        attachments=[AttachmentPart(filename="a.pdf", mime_type="application/pdf", data=b"A")],
    )

    assert msg["Bcc"] == "hidden@example.com"


def test_header_injection_is_still_rejected_with_attachments(frozen_msgid) -> None:
    with pytest.raises(PermanentActionError):
        _build(
            subject="Hello\r\nBcc: attacker@example.com",
            attachments=[AttachmentPart(filename="a.pdf", mime_type="application/pdf", data=b"A")],
        )


def test_total_size_cap_across_multiple_attachment_parts(frozen_msgid, monkeypatch) -> None:
    """10 x 20 MB is refused by the builder, not discovered at SMTP."""
    from services import email_attachments

    monkeypatch.setattr(email_attachments, "max_total_bytes", lambda: 1000)

    with pytest.raises(PermanentActionError) as exc:
        _build(
            attachments=[
                AttachmentPart(
                    filename=f"{i}.bin", mime_type="application/octet-stream", data=b"x" * 600
                )
                for i in range(2)
            ]
        )

    assert exc.value.code == "attachments_too_large"


# ---------------------------------------------------------------------------
# LUM-715 — per-provider outbound ceilings
# ---------------------------------------------------------------------------


def test_the_builder_honours_a_caller_supplied_provider_ceiling(frozen_msgid) -> None:
    with pytest.raises(PermanentActionError) as exc:
        _build(
            attachments=[
                AttachmentPart(
                    filename="a.bin", mime_type="application/octet-stream", data=b"x" * 600
                )
            ],
            max_total_bytes=500,
        )

    assert exc.value.code == "attachments_too_large"


def test_a_provider_ceiling_above_the_global_cap_is_honoured(frozen_msgid, monkeypatch) -> None:
    """Fastmail allows more than the 25 MB default — the send must not be refused
    just because the global constant is lower."""
    from services import email_attachments

    monkeypatch.setattr(email_attachments, "max_total_bytes", lambda: 100)

    msg = _build(
        attachments=[
            AttachmentPart(filename="a.bin", mime_type="application/octet-stream", data=b"x" * 500)
        ],
        max_total_bytes=1000,
    )

    assert msg.get_content_type() == "multipart/mixed"


def test_no_provider_ceiling_falls_back_to_the_global_cap(frozen_msgid, monkeypatch) -> None:
    """An unknown ("Other") host must not get an invented limit."""
    from services import email_attachments

    monkeypatch.setattr(email_attachments, "max_total_bytes", lambda: 100)

    with pytest.raises(PermanentActionError):
        _build(
            attachments=[
                AttachmentPart(
                    filename="a.bin", mime_type="application/octet-stream", data=b"x" * 500
                )
            ],
        )
