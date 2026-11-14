# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Turn a raw RFC822 message into readable plain text (LUM-810).

**Lumogis does not render email HTML.** Reading a message means extracting its
text; sender-controlled bytes never enter a rendered HTML context, so the
containment question a sanitiser would raise is avoided rather than answered
(ADR: *Reading an email means extracting its text, not rendering its HTML*).

This module is the **single body-text authority**. The read view, the
classifier and the summariser all come through `extract_text` — a second
extractor would let the summary and the visible body disagree.

Two constraints shape everything here:

* **It never raises.** Input is attacker-controlled. `feed_monitor`'s poll loop
  breaks on a handler error, so a raise would stall a whole tick rather than
  skip one message; on the read route it would be a 500 on hostile mail.
* **No new dependency, not even a transitive one promoted to direct.** The
  spike measured trafilatura at 71–79% recall against 100% for the stdlib
  walker below, and its failure mode is *silent partial truncation* — it drops
  the first sentence of a short reply, leaving a truncated message the reader
  cannot tell is truncated. `html.parser` is ~40 lines and reads in one
  sitting, which matters for code handling untrusted input.
"""

from __future__ import annotations

import email
import logging
import re
from collections.abc import Iterable
from email.message import Message
from html.parser import HTMLParser

_log = logging.getLogger(__name__)

# Block-level elements: text either side of one of these is not one sentence.
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "br",
        "tr",
        "li",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "table",
        "section",
        "article",
        "header",
        "footer",
        "nav",
        "hr",
        "pre",
    }
)

# Cell boundaries. A tab, not a space: "Trail Runner Pro\t£129.00" keeps the
# columns of a receipt apart, where a space gives "Trail Runner Pro £129.00"
# and no separator at all gives the unreadable "Pro£129".
_CELL_TAGS = frozenset({"td", "th"})

# Never text. `title` in particular reads as a headline if kept.
#
# **Container tags only — every one of these has an end tag.** Suppression is a
# depth counter, so a VOID element here would increment and never decrement,
# and the rest of the message would be silently discarded. `meta` and `link`
# were in this set and did exactly that: one `<meta charset>` — which virtually
# every real HTML email carries — emptied the whole body, for the read view and
# for the classifier alike. They need no entry: void elements contain no text,
# so there is nothing for `handle_data` to suppress.
_DROP_TAGS = frozenset({"script", "style", "head", "title"})

_WS_AROUND_NEWLINE = re.compile(r"[ \t]*\n[ \t]*")
_MANY_NEWLINES = re.compile(r"\n{3,}")
_MANY_SPACES = re.compile(r" {2,}")
# Whitespace *inside* a text run, collapsed at collection time the way a
# browser would. Newlines in the source are not line breaks in the output.
_INNER_WS = re.compile(r"\s+")

# Parser input ceiling, distinct from the adapter's whole-message download cap.
#
# Measured 2026-08-06: 1 MiB of bare "<" costs **2.2 s** in `html.parser` — the
# whole of the route's 2.5 s p95 budget, before any network time, on an input a
# sender chooses. Cost is linear (~2x per doubling, so the CVE-2025-6069
# quadratic blowup is absent), which makes this bound predictable: 256 KiB is
# ~0.6 s worst case.
#
# It is the PARSER that needs bounding, not the download. `_MAX_BODY_BYTES`
# caps `RFC822.SIZE` — the whole message including attachments — so lowering
# THAT to fix parse cost would refuse an ordinary one-line email carrying a
# photo (measured: 1.2 MB message, 0.054 s to parse). Two different objects,
# two different bounds.
#
# Legitimate mail never notices: 256 KiB of markup yields far more than
# `_MAX_BODY_CHARS` of text, so the route's character cap fires first and sets
# `truncated` — the reader is told either way.
_MAX_PARSE_BYTES = 262_144

# Internal fences marking a `<pre>` region so the tidy pass skips it. Private
# use area, and **removed from sender text on the way IN** (`_decode` and
# `_html_to_text`), which is what makes them trustworthy: by the time
# `_collapse_whitespace` runs, every fence in the stream was emitted by this
# parser and is therefore balanced.
#
# Stripping on the way OUT instead was not enough. The exit strip pairs each
# open with the FIRST close in its chunk, so a sender planting two U+F8F1 leaked
# one into the rendered body, and a balanced pair let them exempt their own text
# from whitespace tidying — on the plain-text path as well as the HTML one.
# In-band signalling is only safe when the band is cleaned at the boundary.
_PRE_OPEN = "\uf8f0"
_PRE_CLOSE = "\uf8f1"
_FENCE_STRIP = {ord(_PRE_OPEN): None, ord(_PRE_CLOSE): None}


def _strip_fences(text: str) -> str:
    """Remove the internal `<pre>` fences from sender-supplied text."""
    return text.translate(_FENCE_STRIP)


# Bidi overrides, embeddings and isolates — the Trojan Source vector. These
# reorder displayed text without changing the bytes, so `pay ‮alice‬ now` can
# render as a different instruction than it contains.
_BIDI_CHARS = frozenset("‪‫‬‭‮⁦⁧⁨⁩")

# Sentinel: SO/SI symbols around the codepoint, e.g. `␎U+202E␏`.
_SENTINEL_OPEN = "␎"
_SENTINEL_CLOSE = "␏"


class _TextExtractor(HTMLParser):
    """Block-aware HTML → text.

    Not a sanitiser and not a readability heuristic: it keeps everything the
    message says, including footers and boilerplate. Faithfulness is the point
    — this is someone's actual mail, and a walker that decides what is
    "content" is a walker that silently deletes the part that mattered.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._suppress_depth = 0
        self._pre_depth = 0

    def _tail(self) -> str:
        """The last non-empty thing emitted — what "did we just break a line?"
        actually means. Checking `_out[-1]` alone is wrong once empty strings
        can land in the list."""
        for chunk in reversed(self._out):
            if chunk:
                return chunk
        return ""

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _DROP_TAGS:
            self._suppress_depth += 1
            return
        if tag == "pre":
            self._pre_depth += 1
        if tag in _CELL_TAGS:
            # No separator before the FIRST cell of a row — the row's own
            # newline already separates it from what came before.
            if not self._tail().endswith("\n"):
                self._out.append("\t")
        elif tag in _BLOCK_TAGS:
            self._out.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        # `<br/>` reaches here rather than handle_starttag. Void drop tags
        # (`<meta/>`, `<link/>`) fall through harmlessly: they carry no text.
        if tag in _BLOCK_TAGS:
            self._out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_TAGS:
            self._suppress_depth = max(0, self._suppress_depth - 1)
            return
        if tag == "pre":
            self._pre_depth = max(0, self._pre_depth - 1)
        if tag in _BLOCK_TAGS:
            self._out.append("\n")

    def handle_data(self, data: str) -> None:
        if self._suppress_depth:
            return
        if self._pre_depth:
            # `<pre>` means the whitespace IS the content — an ASCII receipt or
            # a code block is destroyed by collapsing it. Fenced so the tidy
            # pass below cannot touch it.
            self._out.append(f"{_PRE_OPEN}{data}{_PRE_CLOSE}")
            return
        # Inter-tag whitespace is not content — in HTML it is collapsible, and
        # treating it as text is what let a pretty-printed table destroy its own
        # cell separators: the `\t` ended up adjacent to a newline, where the
        # whitespace collapse below eats it. Structure comes from tags only.
        if not data.strip():
            return
        self._out.append(_INNER_WS.sub(" ", data))

    def text(self) -> str:
        return "".join(self._out)


def _html_to_text(html: str) -> str:
    """Block-aware down-conversion. Returns `""` rather than raising."""
    # Belt and braces: `_decode` already cleaned this, but this function is
    # module-level and callable directly, and the fence invariant is what the
    # tidy pass trusts.
    html = _strip_fences(html)
    if len(html) > _MAX_PARSE_BYTES:
        # Truncate rather than refuse: the first 256 KiB of a pathological
        # document still yields the readable part of a real message, and a
        # mid-tag cut is exactly what `html.parser` already tolerates.
        _log.info("email_body_parse_input_truncated size=%s limit=%s", len(html), _MAX_PARSE_BYTES)
        html = html[:_MAX_PARSE_BYTES]
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # noqa: BLE001 - malformed markup must not raise
        _log.warning("email_body_html_parse_failed type=%s", type(exc).__name__)
    return _collapse_whitespace(parser.text())


def _tidy(text: str) -> str:
    text = _WS_AROUND_NEWLINE.sub("\n", text)
    text = _MANY_NEWLINES.sub("\n\n", text)
    return _MANY_SPACES.sub(" ", text)


def _collapse_whitespace(text: str) -> str:
    """Tidy structural whitespace, leaving `<pre>` regions exactly as authored.

    Split rather than regex-guarded: the fences are stripped here, so a sender
    who plants the fence characters in their own text cannot smuggle a region
    past the tidy pass — the worst they achieve is their own text being tidied
    or not.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if _PRE_OPEN not in text:
        return _tidy(text).strip()

    out: list[str] = []
    for chunk in text.split(_PRE_OPEN):
        verbatim, sep, rest = chunk.partition(_PRE_CLOSE)
        if sep:
            out.append(verbatim)
            out.append(_tidy(rest))
        else:
            out.append(_tidy(chunk))
    return "".join(out).strip()


def _decode(part) -> str:
    """One part's text. A charset the platform does not know is not fatal.

    `errors="replace"` covers malformed *bytes*; it does nothing for an
    unregistered *codec name*, which raises `LookupError`. Real mail carries
    plenty of those — `iso-8859-8-i`, `cp-1252`, `windows-874`,
    `x-user-defined`. Letting that escape cost the whole message, including
    sibling parts that decode perfectly well.
    """
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        decoded = payload.decode(charset, "replace")
    except (LookupError, ValueError):
        _log.warning("email_body_unknown_charset charset=%r", charset[:40])
        decoded = payload.decode("utf-8", "replace")
    # The ONE place sender bytes become sender text, so the one place the
    # internal fences have to be cleaned out of them.
    return _strip_fences(decoded)


def _is_attachment(part) -> bool:
    return "attachment" in str(part.get("Content-Disposition", "")).lower()


def _walk_body_parts(part):
    """The parts that make up THIS message's body, and nothing else.

    `Message.walk()` descends into a `message/rfc822` part, so a forwarded mail
    carried as an attachment yields the *inner* message's `text/plain` — which
    then wins over the outer message's HTML body, because plain is preferred.
    The reader is shown the attachment's text as though it were the message.
    Skipping the part but still walking its children (a bare `continue`) does
    not help: `walk()` has already queued them.

    An attachment subtree is skipped whole for the same reason.
    """
    if _is_attachment(part):
        return
    ctype = (part.get_content_type() or "").lower()
    if ctype == "message/rfc822":
        # An embedded message is content the body refers to, not the body.
        return
    if part.is_multipart():
        payload = part.get_payload()
        if isinstance(payload, list):
            for sub in payload:
                yield from _walk_body_parts(sub)
        return
    yield part


def extract_text(raw: bytes | str) -> str:
    """Readable plain text for one raw RFC822 message. **Never raises.**

    Prefers `text/plain`; falls back to down-converting `text/html` when no
    usable plain part exists. A whitespace-only plain part counts as absent —
    some senders ship one as a placeholder alongside the real HTML body.
    """
    try:
        if isinstance(raw, str):
            raw = raw.encode("utf-8", "replace")
        msg = email.message_from_bytes(raw)

        if msg.is_multipart():
            return text_from_parts(_walk_body_parts(msg))
        # Guard on content type. Decoding *any* single-part payload turns an
        # `image/jpeg` into mojibake presented to the reader as text.
        return text_from_parts([msg])
    except Exception as exc:  # noqa: BLE001 - the trust boundary
        _log.warning("email_body_extract_failed type=%s", type(exc).__name__)
        return ""


def text_from_parts(parts: Iterable[Message]) -> str:
    """Readable text from already-isolated MIME parts. **Never raises.**

    Split out of :func:`extract_text` so the adapter can hand over parts it
    fetched *individually* — `BODY.PEEK[1.2]` rather than the whole message —
    without a second copy of the plain-over-HTML preference living there
    (LUM-814). A non-text part contributes nothing, which is what makes it safe
    to pass a lone single-part message through here.

    A whitespace-only plain part counts as **absent**: some senders ship one as
    a placeholder beside the real HTML body, and preferring it would show the
    reader an empty message.
    """
    plain: list[str] = []
    html: list[str] = []
    try:
        for part in parts:
            ctype = (part.get_content_type() or "").lower()
            if ctype == "text/plain":
                plain.append(_decode(part))
            elif ctype == "text/html":
                html.append(_decode(part))
    except Exception as exc:  # noqa: BLE001 - the trust boundary
        _log.warning("email_body_parts_failed type=%s", type(exc).__name__)
        return ""

    joined_plain = _collapse_whitespace("\n".join(plain))
    if joined_plain:
        return joined_plain
    return _html_to_text("\n".join(html)) if html else ""


def annotate_control_chars(text: str) -> str:
    """Make bidi control characters visible. **Marks, never strips.**

    Email is someone's actual message; silently altering it is its own failure
    mode. Gitea took the same warn-and-mark route for Trojan Source.

    **Zero-width characters are deliberately NOT annotated.** ZWJ is structural
    in ordinary emoji sequences and ZWNJ is orthographically *required* in
    Persian, so blanket annotation renders a family emoji or a Farsi sentence
    as sentinel soup — misrepresenting the message in the name of showing it
    faithfully, and training the reader to ignore sentinels so the one real
    U+202E carries no signal. Invisible-text steganography via ZWSP/BOM is a
    real concern, but it is a *prompt-boundary* one (LUM-704), not a display
    one. Homoglyphs are likewise out of scope here: per-script confusability is
    not a control-character problem.
    """
    if not text:
        return text
    if not any(ch in _BIDI_CHARS for ch in text):
        return text
    return "".join(
        f"{_SENTINEL_OPEN}U+{ord(ch):04X}{_SENTINEL_CLOSE}" if ch in _BIDI_CHARS else ch
        for ch in text
    )
