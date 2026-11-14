# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""MIME attachment extraction + filename sanitisation (LUM-695).

Both functions take **attacker-controlled** input: the filename reaches a
``Content-Disposition`` response header on download, and extraction runs over
whatever MIME an arbitrary sender put on the wire. These are the security
boundary of the read half, so they are tested first and heaviest.

The load-bearing case is ``test_walk_parts_index_round_trips_through_nested_multipart``:
``part_index`` is written by one traversal and read by another, and if those two
ever diverge the failure is silent — a plausible-looking file with the wrong
contents. The same helper must produce both sides.
"""

from __future__ import annotations

from email.message import EmailMessage

from services import email_attachments


def _message_with_attachments(*parts: tuple[str, str, bytes]) -> bytes:
    """A ``multipart/mixed`` with a text body plus ``(filename, mime, data)`` parts."""
    msg = EmailMessage()
    msg["From"] = "sender@example.com"
    msg["To"] = "me@example.com"
    msg["Subject"] = "with attachments"
    msg.set_content("Body text here.")
    for filename, mime, data in parts:
        maintype, _, subtype = mime.partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return msg.as_bytes()


# ---------------------------------------------------------------------------
# extract_metadata
# ---------------------------------------------------------------------------


def test_extracts_filename_size_and_mime_for_each_part() -> None:
    raw = _message_with_attachments(
        ("invoice.pdf", "application/pdf", b"%PDF-1.4 fake"),
        ("photo.png", "image/png", b"\x89PNG fake bytes"),
    )

    rows = email_attachments.extract_metadata(raw)

    assert [r.filename for r in rows] == ["invoice.pdf", "photo.png"]
    assert [r.mime_type for r in rows] == ["application/pdf", "image/png"]
    assert [r.size_bytes for r in rows] == [len(b"%PDF-1.4 fake"), len(b"\x89PNG fake bytes")]


def test_inline_body_parts_are_not_attachments() -> None:
    raw = _message_with_attachments(("doc.txt", "text/plain", b"hello"))

    rows = email_attachments.extract_metadata(raw)

    assert len(rows) == 1, "the text/plain BODY part must not be listed as an attachment"
    assert rows[0].filename == "doc.txt"


def test_message_with_no_attachments_yields_empty_list() -> None:
    msg = EmailMessage()
    msg["From"] = "sender@example.com"
    msg["Subject"] = "plain"
    msg.set_content("just a body")

    assert email_attachments.extract_metadata(msg.as_bytes()) == []


def test_malformed_mime_yields_empty_list_never_raises() -> None:
    """One bad message must not break ingest for the whole poll tick."""
    for junk in (b"", b"\x00\x01\x02 not mime at all", b"Subject: x\r\n\r\n" + b"\xff" * 64):
        assert email_attachments.extract_metadata(junk) == []


def test_size_bytes_is_the_decoded_size_not_base64() -> None:
    """A 1000-byte file reports 1000, not the ~1334-byte base64 wire size.

    The caps are user-facing "how big is this file" numbers; comparing an
    encoded size against a 25 MB limit would refuse files that are really 18 MB.
    """
    payload = b"A" * 1000
    raw = _message_with_attachments(("big.bin", "application/octet-stream", payload))

    rows = email_attachments.extract_metadata(raw)

    assert rows[0].size_bytes == 1000


def test_walk_parts_index_round_trips_through_nested_multipart() -> None:
    """The silent-wrong-file regression.

    A ``multipart/mixed`` carrying a ``message/rfc822`` that itself has an
    attachment: the index ``extract_metadata`` wrote must resolve, through the
    SAME helper, to the SAME part. A parallel traversal (e.g. RFC 3501 part
    numbers vs Python's ``msg.walk()``) diverges exactly here.
    """
    inner = EmailMessage()
    inner["From"] = "inner@example.com"
    inner["Subject"] = "forwarded original"
    inner.set_content("inner body")
    inner.add_attachment(
        b"INNER-PAYLOAD", maintype="application", subtype="pdf", filename="inner.pdf"
    )

    outer = EmailMessage()
    outer["From"] = "sender@example.com"
    outer["Subject"] = "fwd"
    outer.set_content("see attached")
    outer.add_attachment(
        b"OUTER-PAYLOAD", maintype="application", subtype="zip", filename="outer.zip"
    )
    outer.add_attachment(inner, filename="original.eml")
    raw = outer.as_bytes()

    rows = email_attachments.extract_metadata(raw)
    assert rows, "nested multipart must still yield attachments"

    for row in rows:
        resolved = email_attachments.resolve_part(raw, row.part_index)
        assert resolved is not None, f"part_index {row.part_index} did not resolve"
        assert resolved.filename == row.filename, (
            "the index written by extract_metadata resolved to a DIFFERENT part — "
            "this is the silent-wrong-file bug"
        )
        assert len(resolved.data) == row.size_bytes


def test_resolve_part_returns_the_bytes_of_the_named_part() -> None:
    raw = _message_with_attachments(
        ("first.pdf", "application/pdf", b"FIRST"),
        ("second.pdf", "application/pdf", b"SECOND"),
    )
    rows = email_attachments.extract_metadata(raw)

    second = email_attachments.resolve_part(raw, rows[1].part_index)

    assert second is not None
    assert second.data == b"SECOND"
    assert second.filename == "second.pdf"


def test_resolve_part_returns_none_for_an_unknown_index() -> None:
    raw = _message_with_attachments(("only.pdf", "application/pdf", b"X"))

    assert email_attachments.resolve_part(raw, 9999) is None


# ---------------------------------------------------------------------------
# sanitise_filename — attacker-controlled input
# ---------------------------------------------------------------------------


def test_sanitises_path_traversal_in_filename() -> None:
    out = email_attachments.sanitise_filename("../../etc/passwd")

    assert "/" not in out and "\\" not in out
    assert ".." not in out
    assert out


def test_sanitises_windows_separators_and_drive_letters() -> None:
    out = email_attachments.sanitise_filename(r"..\..\windows\system32\evil.dll")

    assert "\\" not in out and "/" not in out
    assert ".." not in out


def test_sanitises_null_bytes_and_control_chars() -> None:
    out = email_attachments.sanitise_filename("in\x00voi\x07ce\r\n.pdf")

    assert all(ord(c) >= 32 for c in out)
    assert out == "invoice.pdf"


def test_decodes_rfc2047_encoded_filename() -> None:
    # =?utf-8?B?csOpc3Vtw6kucGRm?= is base64 for "résumé.pdf"
    out = email_attachments.sanitise_filename("=?utf-8?B?csOpc3Vtw6kucGRm?=")

    assert out == "résumé.pdf"


def test_blank_filename_falls_back_to_a_generated_name() -> None:
    assert email_attachments.sanitise_filename("", index=1) == "attachment-1"
    assert email_attachments.sanitise_filename("   ", index=4) == "attachment-4"
    assert email_attachments.sanitise_filename("../..", index=2) == "attachment-2"


def test_over_long_filename_is_capped() -> None:
    out = email_attachments.sanitise_filename("x" * 5000 + ".pdf")

    assert 0 < len(out) <= email_attachments._MAX_FILENAME_CHARS
    assert out.endswith(".pdf"), "the extension must survive truncation"


def test_dot_segments_never_survive_as_the_whole_name() -> None:
    for hostile in (".", "..", "...", "./.", "/"):
        out = email_attachments.sanitise_filename(hostile, index=0)
        assert out not in (".", ".."), f"{hostile!r} sanitised to {out!r}"
        assert "/" not in out


def test_rfc2231_continuation_filename_is_decoded() -> None:
    """``filename*=UTF-8''...`` (RFC 2231) is the other encoded form on the wire."""
    raw = (
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n'
        b"\r\n"
        b"--B\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"body\r\n"
        b"--B\r\n"
        b"Content-Type: application/pdf\r\n"
        b"Content-Disposition: attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf\r\n"
        b"\r\n"
        b"PDFBYTES\r\n"
        b"--B--\r\n"
    )

    rows = email_attachments.extract_metadata(raw)

    assert len(rows) == 1
    assert rows[0].filename == "résumé.pdf"


# ---------------------------------------------------------------------------
# Download policy — size caps and the extension blocklist
# ---------------------------------------------------------------------------


def test_blocked_extensions_are_refused_for_download() -> None:
    for name in ("setup.exe", "run.BAT", "payload.scr", "thing.js", "macro.vbs"):
        assert email_attachments.is_blocked_extension(name), f"{name} should be blocked"


def test_ordinary_document_extensions_are_not_blocked() -> None:
    for name in ("invoice.pdf", "sheet.xlsx", "notes.docx", "photo.png", "archive.zip"):
        assert not email_attachments.is_blocked_extension(name), f"{name} should be servable"


def test_blocklist_ignores_case_and_trailing_dots() -> None:
    assert email_attachments.is_blocked_extension("Setup.ExE")
    assert email_attachments.is_blocked_extension("setup.exe.")


# ---------------------------------------------------------------------------
# Structural — attachment bytes are never logged
# ---------------------------------------------------------------------------


def test_attachment_bytes_are_never_logged(caplog) -> None:
    """Filename and size only.

    A log line carrying attachment content would put a user's private documents
    into an operator-readable log, and would do it silently. Asserted by driving
    the paths that DO log (a malformed message, a resolve miss) with recognisable
    payload bytes and proving they never appear.
    """
    marker = b"SUPERSECRETPAYLOADBYTES"
    raw = _message_with_attachments(("secret.pdf", "application/pdf", marker))

    with caplog.at_level("DEBUG"):
        rows = email_attachments.extract_metadata(raw)
        email_attachments.resolve_part(raw, 0)
        email_attachments.resolve_part(raw, 999)  # a miss
        email_attachments.extract_metadata(b"\xff\xfe not mime")  # a malformed message

    assert rows, "the fixture must actually have produced a row"
    assert marker.decode() not in caplog.text
    assert "SUPERSECRET" not in caplog.text


def test_the_filename_and_size_are_what_reach_the_caller() -> None:
    """The safe substitutes for logging bytes are on the returned rows."""
    raw = _message_with_attachments(("secret.pdf", "application/pdf", b"X" * 42))

    row = email_attachments.extract_metadata(raw)[0]

    assert row.filename == "secret.pdf"
    assert row.size_bytes == 42


# ---------------------------------------------------------------------------
# LUM-713 — inline (cid:) parts are not attachments
# ---------------------------------------------------------------------------


def _message_with_inline_image() -> bytes:
    """A newsletter shape: an HTML body referencing an inline logo by cid:."""
    return (
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: multipart/related; boundary="R"\r\n'
        b"\r\n"
        b"--R\r\n"
        b"Content-Type: text/html\r\n"
        b"\r\n"
        b'<p>Hello</p><img src="cid:logo123">\r\n'
        b"--R\r\n"
        b"Content-Type: image/png\r\n"
        b"Content-Disposition: inline; filename=logo.png\r\n"
        b"Content-ID: <logo123>\r\n"
        b"\r\n"
        b"PNGDATA\r\n"
        b"--R--\r\n"
    )


def test_a_cid_referenced_inline_image_is_not_listed_as_an_attachment() -> None:
    """A signature or newsletter logo is not a file the user "received".

    Listing it puts noise in the attachment list and makes the body read as if
    something is missing. The part is still *in* the message — it is simply not
    an attachment.
    """
    rows = email_attachments.extract_metadata(_message_with_inline_image())

    assert rows == []


def test_an_inline_disposition_WITHOUT_a_content_id_is_still_an_attachment() -> None:
    """Some senders mark real attachments ``inline``. Only a cid: handle — i.e. a
    part the body can actually reference — makes it genuinely inline."""
    raw = (
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n'
        b"\r\n"
        b"--B\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"body\r\n"
        b"--B\r\n"
        b"Content-Type: application/pdf\r\n"
        b"Content-Disposition: inline; filename=invoice.pdf\r\n"
        b"\r\n"
        b"PDFBYTES\r\n"
        b"--B--\r\n"
    )

    rows = email_attachments.extract_metadata(raw)

    assert [r.filename for r in rows] == ["invoice.pdf"]


def test_an_explicit_attachment_disposition_wins_over_a_content_id() -> None:
    """``Content-Disposition: attachment`` is the sender saying "this is a file",
    and it must not be silently hidden just because a Content-ID exists."""
    raw = (
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n'
        b"\r\n"
        b"--B\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"body\r\n"
        b"--B\r\n"
        b"Content-Type: image/png\r\n"
        b"Content-Disposition: attachment; filename=chart.png\r\n"
        b"Content-ID: <chart99>\r\n"
        b"\r\n"
        b"PNGDATA\r\n"
        b"--B--\r\n"
    )

    rows = email_attachments.extract_metadata(raw)

    assert [r.filename for r in rows] == ["chart.png"]


def test_hiding_inline_parts_does_not_shift_the_indexes_of_real_attachments() -> None:
    """The part_index contract must survive the reclassification.

    An index written before this change still has to resolve to the same file —
    which it does, because both sides go through the one `_walk_parts`.
    """
    raw = (
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n'
        b"\r\n"
        b"--B\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"body\r\n"
        b"--B\r\n"
        b"Content-Type: image/png\r\n"
        b"Content-Disposition: inline; filename=logo.png\r\n"
        b"Content-ID: <logo1>\r\n"
        b"\r\n"
        b"LOGO\r\n"
        b"--B\r\n"
        b"Content-Type: application/pdf\r\n"
        b"Content-Disposition: attachment; filename=real.pdf\r\n"
        b"\r\n"
        b"REALPDF\r\n"
        b"--B--\r\n"
    )

    rows = email_attachments.extract_metadata(raw)
    assert [r.filename for r in rows] == ["real.pdf"]

    resolved = email_attachments.resolve_part(raw, rows[0].part_index)
    assert resolved is not None and resolved.data == b"REALPDF"


def test_the_two_lists_partition_the_message_with_no_part_in_both() -> None:
    """The double-display guard, asserted on a whole message (LUM-713).

    `is_inline_image` and `extract_metadata` answer about the same parts from
    opposite sides. Every part must land in at most one list: a file shown as a
    download AND rendered in the strip is the same picture twice.
    """
    import email as _email

    from services.email_attachments import is_inline_image

    raw = (
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n'
        b"\r\n"
        b"--B\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"body\r\n"
        b"--B\r\n"
        b"Content-Type: image/png\r\n"
        b"Content-Disposition: inline\r\n"
        b"Content-ID: <logo1>\r\n"
        b"\r\n"
        b"LOGO\r\n"
        b"--B\r\n"
        # A real photo the sender ALSO gave a Content-ID. The tempting predicate
        # "image and a Content-ID" would put this in both lists.
        b"Content-Type: image/png\r\n"
        b"Content-Disposition: attachment; filename=holiday.png\r\n"
        b"Content-ID: <photo1>\r\n"
        b"\r\n"
        b"PHOTO\r\n"
        b"--B--\r\n"
    )

    attachments = {r.filename for r in email_attachments.extract_metadata(raw)}
    inline = [p for p in _email.message_from_bytes(raw).walk() if is_inline_image(p)]

    assert attachments == {"holiday.png"}
    assert [p.get("Content-ID") for p in inline] == ["<logo1>"]
