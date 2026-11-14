# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Inline image identification and sniffing (LUM-713).

The security property this file exists for: **the declared type never decides
anything.** A sender picks `Content-Type`, so an SVG announced as `image/png`
must be refused — SVG is a script container, and it is the one format where
"it says it's an image" is the attack.

Refusal is by **allowlist of binary signatures**, not by blocklist. SVG has no
magic number, so only an allowlist can exclude it; a blocklist would admit it
the moment a sender declared something else.
"""

from __future__ import annotations

import pytest
from services.email_attachments import is_inline_image
from services.email_inline_images import inline_images_from_parts
from services.email_inline_images import sniff_image_type

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 32


def _msg(raw: bytes):
    import email

    return email.message_from_bytes(raw)


def _part(ctype: str, body: bytes, *, cid: str | None = "<i1>", disp: str = "inline"):
    import email
    from base64 import b64encode

    head = f"Content-Type: {ctype}\r\n"
    if disp:
        head += f"Content-Disposition: {disp}\r\n"
    if cid:
        head += f"Content-ID: {cid}\r\n"
    head += "Content-Transfer-Encoding: base64\r\n"
    return email.message_from_bytes((head + "\r\n").encode() + b64encode(body))


# ---------------------------------------------------------------------------
# Sniffing — the trust boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "data,expected",
    [(PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"), (WEBP, "image/webp")],
)
def test_each_accepted_format_sniffs_to_its_canonical_type(data, expected):
    assert sniff_image_type(data) == expected


def test_svg_is_refused_however_it_is_declared():
    """**The security test.** SVG is a script container with no magic number."""
    assert sniff_image_type(b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>') is None
    assert sniff_image_type(b"<?xml version='1.0'?><svg></svg>") is None


def test_html_is_refused():
    assert sniff_image_type(b"<html><script>alert(1)</script></html>") is None


def test_a_truncated_signature_is_refused_not_guessed():
    """Half a PNG header is not a PNG. Guessing here is how a sniffer becomes
    a bypass."""
    assert sniff_image_type(b"\x89PNG") is None
    assert sniff_image_type(b"") is None


def test_riff_that_is_not_webp_is_refused():
    """RIFF is a container — WAV and AVI share the prefix. Matching on `RIFF`
    alone would accept an audio file as an image."""
    assert sniff_image_type(b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 20) is None


@pytest.mark.parametrize(
    "payload", [b"", b"\x00", b"\xff" * 64, bytes(range(256)), b"\x89PNG\r\n\x1a"]
)
def test_sniff_never_raises(payload):
    """It reads attacker-controlled bytes."""
    assert sniff_image_type(payload) in (None, "image/png", "image/jpeg", "image/gif", "image/webp")


# ---------------------------------------------------------------------------
# Membership — one rule, shared with the attachment classifier
# ---------------------------------------------------------------------------


def test_a_cid_inline_image_is_an_inline_image():
    assert is_inline_image(_part("image/png", PNG)) is True


def test_a_cid_part_marked_attachment_is_NOT_an_inline_image():
    """The double-display case.

    `_is_attachment` gives an explicit `attachment` disposition priority over a
    Content-ID, so such a part IS in the attachment list. A looser
    "IMAGE and Content-ID" predicate would put it in the strip as well, and the
    user would see the same file twice.
    """
    p = _part("image/png", PNG, disp="attachment; filename=logo.png")
    assert is_inline_image(p) is False


def test_an_inline_part_without_a_content_id_is_not_an_inline_image():
    """Only a `cid:` handle proves the body could address the part — the same
    reasoning the shipped classifier uses."""
    assert is_inline_image(_part("image/png", PNG, cid=None)) is False


def test_a_non_image_with_a_content_id_is_not_an_inline_image():
    assert is_inline_image(_part("application/pdf", b"%PDF-1.4", cid="<x>")) is False


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def test_inline_images_are_returned_with_the_sniffed_type():
    out = inline_images_from_parts([_part("image/png", PNG)]).images
    assert len(out) == 1
    assert out[0].mime_type == "image/png"
    assert out[0].content_id == "<i1>"
    assert out[0].size_bytes == len(PNG)


def test_the_declared_type_is_never_believed():
    """An SVG announced as PNG is dropped, not relabelled and not rendered."""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>'
    assert inline_images_from_parts([_part("image/png", svg)]).images == []


def test_a_part_that_does_not_sniff_is_dropped_silently():
    assert inline_images_from_parts([_part("image/png", b"not an image at all")]).images == []


def test_ordering_follows_the_message():
    parts = [_part("image/png", PNG, cid="<a>"), _part("image/gif", GIF, cid="<b>")]
    assert [i.content_id for i in inline_images_from_parts(parts).images] == ["<a>", "<b>"]


def test_caps_drop_the_excess_and_report_it():
    from services.email_inline_images import MAX_INLINE_IMAGES

    parts = [_part("image/png", PNG, cid=f"<{n}>") for n in range(MAX_INLINE_IMAGES + 3)]
    out, truncated = inline_images_from_parts(parts)
    assert len(out) == MAX_INLINE_IMAGES
    assert truncated is True


def test_exactly_the_cap_is_not_truncated():
    """The off-by-one that would make every six-image message claim a seventh."""
    from services.email_inline_images import MAX_INLINE_IMAGES

    parts = [_part("image/png", PNG, cid=f"<{n}>") for n in range(MAX_INLINE_IMAGES)]
    out, truncated = inline_images_from_parts(parts)
    assert len(out) == MAX_INLINE_IMAGES
    assert truncated is False


def test_a_single_oversized_image_is_dropped_but_the_others_survive():
    from services.email_inline_images import MAX_ONE_INLINE_BYTES

    big = PNG[:8] + b"\x00" * (MAX_ONE_INLINE_BYTES + 1)
    out, truncated = inline_images_from_parts(
        [_part("image/png", big), _part("image/gif", GIF, cid="<ok>")]
    )
    assert [i.content_id for i in out] == ["<ok>"]
    assert truncated is True


def test_a_refusal_PAST_the_count_cap_is_still_not_a_truncation():
    """The cap must fire on real images only.

    Checking the count before sniffing is cheaper — no decode — and wrong: a
    message with six images plus one SVG-declared-as-PNG would tell the reader
    an image "wasn't shown", when what actually happened is that an attack was
    blocked. Nothing was lost, so nothing may be reported as lost.
    """
    from services.email_inline_images import MAX_INLINE_IMAGES

    parts = [_part("image/png", PNG, cid=f"<{n}>") for n in range(MAX_INLINE_IMAGES)]
    parts.append(_part("image/png", b"<svg/>" + b"x" * 64, cid="<evil>"))

    out, truncated = inline_images_from_parts(parts)

    assert len(out) == MAX_INLINE_IMAGES
    assert truncated is False


def test_a_refused_part_is_NOT_reported_as_truncated():
    """A refusal and a cap are different events and the UI says different things.

    Counting a 4 MB SVG-declared-PNG as "1 image not shown" would report a
    blocked attack to the user as missing content.
    """
    from services.email_inline_images import MAX_ONE_INLINE_BYTES

    huge_svg = b"<svg>" + b"a" * (MAX_ONE_INLINE_BYTES + 1)
    out, truncated = inline_images_from_parts([_part("image/png", huge_svg)])
    assert out == []
    assert truncated is False


def test_the_total_byte_budget_stops_the_list():
    from services.email_inline_images import MAX_INLINE_BYTES
    from services.email_inline_images import MAX_ONE_INLINE_BYTES

    each = PNG[:8] + b"\x00" * (MAX_ONE_INLINE_BYTES - 8)
    n = (MAX_INLINE_BYTES // MAX_ONE_INLINE_BYTES) + 2
    out, truncated = inline_images_from_parts(
        [_part("image/png", each, cid=f"<{i}>") for i in range(n)]
    )
    assert sum(i.size_bytes for i in out) <= MAX_INLINE_BYTES
    assert truncated is True


def test_data_uri_carries_the_sniffed_type_and_decodes_back():
    from base64 import b64decode

    out = inline_images_from_parts([_part("image/png", PNG)]).images
    prefix = "data:image/png;base64,"
    assert out[0].data_uri.startswith(prefix)
    assert b64decode(out[0].data_uri[len(prefix) :]) == PNG


def test_inline_images_from_parts_never_raises_on_hostile_parts():
    class _Exploding:
        def get_content_type(self):
            raise RuntimeError("boom")

    assert inline_images_from_parts([_Exploding()]) == ([], False)
