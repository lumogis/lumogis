# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Email attachment MIME policy — extraction, sanitisation, size caps (LUM-695).

This module owns the **shared MIME walk**. Both halves of the read path go
through :func:`_walk_parts`:

* :func:`extract_metadata` writes ``part_index`` during full-ingest, and
* :func:`resolve_part` reads it back when the user opens an attachment.

That they are the *same* traversal is the point. ``part_index`` is this helper's
own enumeration index — **not** an RFC 3501 IMAP part number. The two numberings
diverge exactly on nested ``multipart`` and ``message/rfc822`` messages (i.e.
forwarded mail with attachments), and a mismatch fails *silently*: no exception,
just a plausible-looking file with the wrong contents. Consistency here is by
construction, not by care.

The helper lives in ``services/`` rather than in the IMAP adapter so the
dependency runs ``adapters/email_imap_source.py`` → this module. Putting it in
the adapter would force this module to import ``EmailImapSource``, which is the
wrong direction (``services/`` must never import adapters).

Both public entry points take **attacker-controlled** input. Filenames reach a
``Content-Disposition`` response header, so they are sanitised before storage,
and extraction never raises — one hostile message must not break the poll tick.
"""

from __future__ import annotations

import email
import logging
import os
import re
from dataclasses import dataclass
from email.header import decode_header
from email.message import Message
from typing import Iterator
from typing import Optional

_log = logging.getLogger(__name__)

# Aligned to the common provider ceiling (Gmail is 25 MB; others differ — a
# per-provider value needs the provider registry to carry a size field, which is
# a follow-up). Enforced on SEND for the whole message, and on READ per
# attachment: over the cap the FETCH is refused rather than streamed.
_MAX_ATTACHMENT_BYTES: int = int(os.environ.get("EMAIL_ATTACHMENT_MAX_MB", "25")) * 1024 * 1024
_MAX_TOTAL_BYTES: int = _MAX_ATTACHMENT_BYTES

# A DIFFERENT object with a DIFFERENT bound: opening one 10 KB attachment pulls
# the WHOLE message over IMAP (``BODY.PEEK[]``), and ``client.fetch`` buffers —
# nothing streams. A hostile 400 MB message carrying a small attachment passes
# the attachment check and would still exhaust memory, so the fetch is bounded by
# the variable that actually describes its cost.
_MAX_MESSAGE_FETCH_BYTES: int = (
    int(os.environ.get("EMAIL_ATTACHMENT_MAX_MESSAGE_FETCH_MB", "50")) * 1024 * 1024
)

_MAX_FILENAME_CHARS = 180

# Refused as a DOWNLOAD. This is not virus scanning and is not presented as such
# — it is a refusal to become a convenient delivery vector for something the
# user's own mail client would have quarantined.
_BLOCKED_EXTENSIONS: frozenset[str] = frozenset(
    {
        "exe",
        "scr",
        "bat",
        "cmd",
        "com",
        "pif",
        "cpl",
        "msi",
        "msp",
        "msc",
        "jar",
        "js",
        "jse",
        "vb",
        "vbs",
        "vbe",
        "ws",
        "wsf",
        "wsh",
        "wsc",
        "ps1",
        "ps1xml",
        "ps2",
        "psc1",
        "psc2",
        "scf",
        "lnk",
        "inf",
        "reg",
        "hta",
        "dll",
        "sys",
        "drv",
        "app",
        "dmg",
        "pkg",
        "deb",
        "rpm",
    }
)

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class AttachmentMeta:
    """One attachment PART of a message. Metadata only — bytes are never stored."""

    filename: str
    mime_type: str
    size_bytes: int
    part_index: int


@dataclass(frozen=True)
class ResolvedPart:
    """A part located by ``part_index``, with its decoded bytes."""

    filename: str
    mime_type: str
    data: bytes
    part_index: int


# ---------------------------------------------------------------------------
# The shared walk — the ONE traversal both halves use
# ---------------------------------------------------------------------------


def is_inline_image(part: Message) -> bool:
    """True when `part` is an image the body could reference by `cid:` (LUM-713).

    **Derived from `_is_attachment`, not from a parallel rule.** The tempting
    predicate — "image maintype and a Content-ID" — is subtly wrong: a sender
    who marks a real photo `Content-Disposition: attachment` *and* gives it a
    Content-ID has one that `_is_attachment` correctly calls a file, so the
    looser rule would list it in the attachment list **and** the inline strip.
    The user would see the same picture twice, once as a download.

    So: an image, carrying a **Content-ID**, that `_is_attachment` does not
    claim. The Content-ID is required in its own right — "not an attachment" is
    weaker than it looks, because an `inline` part with no Content-ID and no
    filename is also not an attachment, and nothing in the body can address it.
    Only a `cid:` handle proves it could be referenced, which is exactly the
    reasoning the shipped classifier uses at the rule below.
    """
    if part.get_content_maintype() != "image":
        return False
    if not part.get("Content-ID"):
        return False
    return not _is_attachment(part)


def _walk_parts(raw: bytes | str) -> Iterator[tuple[int, Message]]:
    """Yield ``(part_index, part)`` for every ATTACHMENT part of ``raw``.

    ``part_index`` enumerates only the parts this function yields, so the index
    written at extract time is the index resolved at fetch time **by
    construction**. Non-attachment parts (the body, multipart containers) are
    skipped without consuming an index.
    """
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "replace")
    msg = email.message_from_bytes(raw)
    index = 0
    for part in msg.walk():
        if not _is_attachment(part):
            continue
        yield index, part
        index += 1


def _is_attachment(part: Message) -> bool:
    """True for a part a user would call "an attachment".

    A multipart container is never one. Otherwise the rule is the same seam
    ``_body_to_text`` already uses in reverse: an explicit ``attachment``
    disposition, or any non-container part that carries a filename (some senders
    mark real attachments ``inline`` but still name them).
    """
    if part.get_content_maintype() == "multipart":
        return False
    disposition = str(part.get("Content-Disposition", "") or "").lower()
    if "attachment" in disposition:
        # The sender explicitly said "this is a file". That wins even when the
        # part also carries a Content-ID — hiding it because it *could* be
        # referenced inline would silently drop a real attachment.
        return True
    # LUM-713: a part the body can reference by `cid:` is INLINE, not an
    # attachment — a signature image or newsletter logo is not a file the user
    # received. Requiring the Content-ID (not just `Content-Disposition: inline`)
    # is deliberate: plenty of senders mark genuine attachments `inline`, and
    # only a cid: handle proves the body can actually address the part.
    if part.get("Content-ID") and "inline" in disposition:
        return False
    # get_filename() covers both the plain `filename=` and RFC 2231 `filename*=`
    # forms, and returns None when the part is an unnamed body part.
    return part.get_filename() is not None


def _decoded_payload(part: Message) -> bytes:
    """The DECODED bytes of ``part`` — never the base64 wire form.

    ``message/rfc822`` parts have a list payload (the nested message), which
    ``get_payload(decode=True)`` cannot decode; serialise the nested message
    instead so an attached ``.eml`` reports and downloads its whole self.
    """
    if part.get_content_type() == "message/rfc822":
        nested = part.get_payload()
        if isinstance(nested, list) and nested:
            return nested[0].as_bytes()
    payload = part.get_payload(decode=True)
    return payload if isinstance(payload, bytes) else b""


# ---------------------------------------------------------------------------
# Filename sanitisation — attacker-controlled
# ---------------------------------------------------------------------------


def sanitise_filename(raw: Optional[str], *, index: int = 0) -> str:
    """Return a safe display filename for ``raw``.

    Decodes RFC 2047 encoded words, strips path separators and control
    characters, caps the length (preserving the extension), and falls back to
    ``attachment-{index}`` when nothing usable survives.

    This value reaches a ``Content-Disposition`` header on download. It is
    **not** used as an on-disk path component anywhere — the staging service
    uses a server-controlled leaf — but it is sanitised as though it were,
    because header injection and traversal are the same class of trust error.
    """
    text = _decode_encoded_words(raw or "")
    text = _CONTROL_CHARS.sub("", text)
    # Take the last segment of BOTH separator conventions: a Windows client can
    # send `C:\Users\x\evil.dll`, a POSIX one `../../etc/passwd`.
    text = text.replace("\\", "/").rsplit("/", 1)[-1]
    text = text.strip().strip(".").strip()
    if not text or text in (".", ".."):
        return f"attachment-{index}"
    return _cap_length(text)


def _decode_encoded_words(raw: str) -> str:
    """Decode RFC 2047 ``=?charset?enc?...?=`` words; return ``raw`` on any failure."""
    try:
        parts = decode_header(raw)
    except Exception:  # noqa: BLE001 - a hostile header must not raise here
        return raw
    out: list[str] = []
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            out.append(chunk.decode(charset or "utf-8", "replace"))
        else:
            out.append(chunk)
    return "".join(out)


def _cap_length(name: str) -> str:
    """Bound the length, keeping the extension so the file still opens."""
    if len(name) <= _MAX_FILENAME_CHARS:
        return name
    stem, dot, ext = name.rpartition(".")
    if dot and 0 < len(ext) <= 12:
        keep = _MAX_FILENAME_CHARS - len(ext) - 1
        return f"{stem[:keep]}.{ext}"
    return name[:_MAX_FILENAME_CHARS]


def is_blocked_extension(filename: str) -> bool:
    """True when ``filename``'s extension is on the download blocklist."""
    stem = (filename or "").strip().rstrip(".")
    _, dot, ext = stem.rpartition(".")
    return bool(dot) and ext.lower() in _BLOCKED_EXTENSIONS


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_metadata(raw: bytes | str) -> list[AttachmentMeta]:
    """Attachment metadata for one raw message. **Never raises.**

    A malformed message yields ``[]`` — ingest must not fail over metadata, and
    ``feed_monitor``'s poll loop breaks on a handler error, so a raise here
    would stall the whole tick rather than skip one message.
    """
    rows: list[AttachmentMeta] = []
    try:
        for index, part in _walk_parts(raw):
            payload = _decoded_payload(part)
            rows.append(
                AttachmentMeta(
                    filename=sanitise_filename(part.get_filename(), index=index),
                    mime_type=(part.get_content_type() or "").lower(),
                    size_bytes=len(payload),
                    part_index=index,
                )
            )
    except Exception as exc:  # noqa: BLE001 - one bad message must not break ingest
        # Filename + type only. Attachment BYTES are never logged.
        _log.warning("email_attachment_extract_failed type=%s", type(exc).__name__)
        return []
    return rows


def resolve_part(raw: bytes | str, part_index: int) -> Optional[ResolvedPart]:
    """The part ``extract_metadata`` recorded as ``part_index``, with its bytes.

    Returns ``None`` when the index does not resolve (the message changed, or
    the caller supplied an index we never wrote). Uses the SAME ``_walk_parts``
    that wrote the index — see the module docstring for why that matters.
    """
    try:
        for index, part in _walk_parts(raw):
            if index != part_index:
                continue
            return ResolvedPart(
                filename=sanitise_filename(part.get_filename(), index=index),
                mime_type=(part.get_content_type() or "").lower(),
                data=_decoded_payload(part),
                part_index=index,
            )
    except Exception as exc:  # noqa: BLE001
        _log.warning("email_attachment_resolve_failed type=%s", type(exc).__name__)
        return None
    return None


def resolve_parts(raw: bytes | str, part_indexes: list[int]) -> dict[int, ResolvedPart]:
    """Resolve SEVERAL indexes from ONE parse. ``{index: part}``, missing omitted.

    The batch companion to :func:`resolve_part`, for callers that need more than
    one attachment off the same message (forwarding an original's files). Walks
    once rather than once per index, and — critically — walks with the SAME
    helper that wrote the indexes, so the batch cannot drift from the single.
    """
    out: dict[int, ResolvedPart] = {}
    wanted = set(int(i) for i in part_indexes)
    if not wanted:
        return out
    try:
        for index, part in _walk_parts(raw):
            if index not in wanted:
                continue
            out[index] = ResolvedPart(
                filename=sanitise_filename(part.get_filename(), index=index),
                mime_type=(part.get_content_type() or "").lower(),
                data=_decoded_payload(part),
                part_index=index,
            )
            if len(out) == len(wanted):
                break
    except Exception as exc:  # noqa: BLE001
        _log.warning("email_attachment_resolve_failed type=%s", type(exc).__name__)
        return {}
    return out


def max_attachment_bytes() -> int:
    """Per-attachment cap (read + send). Read at call time so tests can patch it."""
    return _MAX_ATTACHMENT_BYTES


def max_total_bytes() -> int:
    """Whole-message outbound cap across every attachment on one send."""
    return _MAX_TOTAL_BYTES


def max_message_fetch_bytes() -> int:
    """Whole-message ceiling for an on-demand fetch (``RFC822.SIZE`` precheck)."""
    return _MAX_MESSAGE_FETCH_BYTES
