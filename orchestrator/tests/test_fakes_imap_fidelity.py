# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The IMAP fake's own fidelity to RFC 3501.

Every named-part test in this repo — the LUM-814 text-only path, the LUM-713
inline image strip — is only as trustworthy as the structure `_fakes.py`
invents. A fake that gets the numbering wrong in the same way the code under
test does will certify the bug, and no amount of testing above it will notice.

The specific hazard is `message/rfc822`. **Python's `is_multipart()` returns
True for it** (its payload is a list holding the encapsulated message), but a
real server does not treat it as a multipart: RFC 3501 gives it its own
`body-type-msg` production, and numbers the encapsulated message's parts under
the PARENT. Branching on `is_multipart()` therefore yields `2.1.2` where a real
server says `2.2` — one level too deep, silently, only for forwarded mail.
"""

from __future__ import annotations

import email as _email
from email.message import EmailMessage

from tests._fakes import _bodystructure
from tests._fakes import _walk_numbered


def _forwarded() -> bytes:
    """A forwarded message: a note, then someone else's mail carrying a logo."""
    inner = EmailMessage()
    inner["From"] = "school@example.com"
    inner["Subject"] = "sports day"
    inner.set_content("Sports day is Thursday.")
    logo = EmailMessage()
    logo.set_content(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, maintype="image", subtype="png")
    logo.add_header("Content-ID", "<logo1>")
    logo.add_header("Content-Disposition", "inline")
    inner.make_mixed()
    inner.attach(logo)

    outer = EmailMessage()
    outer["From"] = "me@example.com"
    outer["Subject"] = "Fwd: sports day"
    outer["Message-ID"] = "<fwd@example.com>"
    outer.set_content("See below.")
    outer.make_mixed()
    wrapper = EmailMessage()
    wrapper.set_content(inner)  # message/rfc822
    outer.attach(wrapper)
    return outer.as_bytes()


def test_a_forwarded_messages_parts_are_numbered_under_the_parent() -> None:
    """`2.1` and `2.2`, never `2.1.1` and `2.1.2`."""
    numbers = [n for n, _ in _walk_numbered(_email.message_from_bytes(_forwarded()))]

    assert numbers == ["1", "2.1", "2.2"]


def test_an_encapsulated_message_is_a_single_part_not_a_multipart() -> None:
    """`body-type-msg`, so `MESSAGE` sits at field 1 and `is_multipart` is
    False — the shape every walker in the adapter branches on."""
    struct = _bodystructure(_forwarded())

    assert struct.is_multipart
    encapsulated = struct[0][1]
    assert not encapsulated.is_multipart
    assert encapsulated[0] == b"MESSAGE"
    assert encapsulated[1] == b"RFC822"


def test_the_size_field_is_encoded_octets_not_decoded() -> None:
    """A real server reports the octets it would transmit, so a base64 part is
    ~4/3 of its decoded size. The adapter may only use this as a transfer
    bound; treating it as a decoded size dropped a whole size band."""
    payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * 3000

    msg = EmailMessage()
    msg["From"] = "a@example.com"
    msg.set_content("text")
    part = EmailMessage()
    part.set_content(payload, maintype="image", subtype="png")
    part.add_header("Content-ID", "<i>")
    part.add_header("Content-Disposition", "inline")
    msg.make_mixed()
    msg.attach(part)

    image = _bodystructure(msg.as_bytes())[0][1]

    assert image[6] > len(payload)


def test_a_disposition_is_reported_so_the_walk_can_see_it() -> None:
    """Field 9 (index 8) of a non-text single part. Absent from the fake, the
    oversized read path could not tell an inline logo from an attached photo
    that happens to carry a Content-ID."""
    msg = EmailMessage()
    msg["From"] = "a@example.com"
    msg.set_content("text")
    part = EmailMessage()
    part.set_content(b"\x89PNG\r\n\x1a\n", maintype="image", subtype="png")
    part.add_header("Content-ID", "<i>")
    part.add_header("Content-Disposition", "attachment", filename="photo.png")
    msg.make_mixed()
    msg.attach(part)

    image = _bodystructure(msg.as_bytes())[0][1]

    assert image[8][0] == b"ATTACHMENT"
