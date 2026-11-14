# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The images a sender embedded *in* a message, identified by their bytes (LUM-713).

Lumogis does not render sender HTML (ADR-207), so a `cid:` inline part has no
rendered context to appear in — and the attachment classifier deliberately
excludes it, because a signature logo is not a file the user received. Between
the two, inline images were visible **nowhere**. This module is how they become
visible: below the extracted text, as bytes we identified ourselves.

**The declared `Content-Type` never decides anything.** A sender picks it, so an
SVG announced as `image/png` would otherwise reach an `<img>` — and SVG is a
script container, the one format where "it says it's an image" is the attack.

**Identification is an allowlist of binary signatures, never a blocklist.** SVG
has no magic number: only an allowlist can exclude it. A blocklist would admit
it the moment a sender declared something else.

**Signatures, not a decoder.** Pillow is available (`adapters/ocr_extractor.py`)
and is deliberately not used: *decoding* an attacker's image is a far larger
attack surface than reading its first twelve bytes, and Pillow's decoders have a
long CVE history. We need identification, not decoding.
"""

from __future__ import annotations

import logging
from base64 import b64encode
from dataclasses import dataclass
from typing import NamedTuple

_log = logging.getLogger(__name__)

# Bounded because these ride the `/body` response as `data:` URIs. Base64
# inflates by ~4/3, so the worst case adds ~2.7 MiB to a response — reached only
# by a message that really is a photo album. A signature logo is 5–50 KB.
MAX_INLINE_IMAGES = 6
MAX_INLINE_BYTES = 2 * 1024 * 1024
MAX_ONE_INLINE_BYTES = 512 * 1024

# The same two bounds expressed in ENCODED octets, for the one caller that must
# decide before it has the bytes: the oversized read path sees only a
# `BODYSTRUCTURE`, whose size field is the encoded octet count (RFC 3501).
#
# They are deliberately LOOSE. Comparing an encoded size against a decoded cap
# drops any image between about 384 KiB and 512 KiB on that path while the
# normal path shows it — a divergence, which is the one thing the strip must
# not have. So these bound the *transfer* only, and the collector above makes
# every real decision on decoded bytes. 4x covers base64 (~1.37x with line
# breaks) and quoted-printable's binary worst case (3x) with room to spare.
_ENCODING_OVERHEAD = 4
MAX_ONE_INLINE_TRANSFER_BYTES = MAX_ONE_INLINE_BYTES * _ENCODING_OVERHEAD
MAX_INLINE_TRANSFER_BYTES = MAX_INLINE_BYTES * _ENCODING_OVERHEAD

# Longest signature we compare against, so a short read cannot match by accident.
_SNIFF_BYTES = 16


@dataclass(frozen=True)
class InlineImage:
    """One identified inline image, ready to render."""

    content_id: str
    mime_type: str  # the SNIFFED type — the declared one never reaches here
    size_bytes: int  # decoded, pre-base64
    data_uri: str


class InlineImageSet(NamedTuple):
    """What survived, and whether a **cap** — not a refusal — dropped anything.

    The two are reported together because only the collector can tell them
    apart, and the UI must not conflate them: a refused part was never an
    image the user could have seen, while a capped one was.
    """

    images: list[InlineImage]
    truncated: bool


def sniff_image_type(data: bytes) -> str | None:
    """The image type these bytes actually are, or ``None`` to refuse.

    ``None`` means **refuse**, never "fall back to what the sender said".
    Never raises: the input is attacker-controlled.
    """
    try:
        head = bytes(data[:_SNIFF_BYTES])
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if head.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if head.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        # RIFF is a container shared with WAV and AVI, so the form type at
        # offset 8 is the part that means "image". Matching `RIFF` alone would
        # accept an audio file as a picture.
        if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
            return "image/webp"
        return None
    except Exception as exc:  # noqa: BLE001 - the trust boundary
        _log.warning("email_inline_sniff_failed type=%s", type(exc).__name__)
        return None


def inline_images_from_parts(parts) -> InlineImageSet:
    """Identified, bounded inline images from already-decoded MIME parts.

    Takes parts rather than a message or a `BODYSTRUCTURE` so the **normal** and
    **oversized** read paths can share it: one has the whole message parsed, the
    other has individually-fetched parts, and both end up holding parts.

    A part that does not sniff is **dropped, not reported** — an unrenderable
    part should be absent, not a broken-image icon, and ``truncated`` stays
    false: nothing the user could have seen went missing. A part a **cap**
    dropped is the opposite case, and does set it. Never raises.
    """
    from services.email_attachments import is_inline_image

    out: list[InlineImage] = []
    truncated = False
    total = 0
    for part in parts:
        try:
            if not is_inline_image(part):
                continue
            payload = part.get_payload(decode=True) or b""
            if not payload:
                continue
            # Sniff BEFORE **every** cap, including the count. A refusal and a
            # truncation are different events and the UI says different things,
            # so no cap may fire on a part we have not first established is a
            # real image: a 4 MB SVG declared `image/png` is a refusal, and
            # reporting it as "1 image not shown" would tell the reader a
            # blocked attack was lost mail. That costs one decode per part past
            # the cap — cheap, and the alternative is the wrong answer.
            mime_type = sniff_image_type(payload)
            if mime_type is None:
                continue
            if len(out) >= MAX_INLINE_IMAGES:
                truncated = True
                break
            if len(payload) > MAX_ONE_INLINE_BYTES:
                truncated = True
                continue
            if total + len(payload) > MAX_INLINE_BYTES:
                # Budget spent. Stop rather than skip-and-continue: a smaller
                # image later would otherwise jump the queue past a larger one
                # the sender put first.
                truncated = True
                break
            total += len(payload)
            out.append(
                InlineImage(
                    content_id=str(part.get("Content-ID") or "").strip(),
                    mime_type=mime_type,
                    size_bytes=len(payload),
                    data_uri=f"data:{mime_type};base64,{b64encode(payload).decode('ascii')}",
                )
            )
        except Exception as exc:  # noqa: BLE001 - one bad part costs itself only
            _log.warning("email_inline_part_skipped type=%s", type(exc).__name__)
            continue
    return InlineImageSet(out, truncated)
