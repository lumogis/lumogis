# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Quoted-reply-history removal for the email search index (LUM-826).

`extract_text` returns the whole message, quoted history included, and LUM-819
stored that verbatim. So a sentence written once, in the first message of a
thread, is stored again in every reply that quotes it — measured at ~8.6×
amplification, and one phrase returning five hits for a single conversation.

**This module is deliberately timid, because the failure modes are asymmetric.**
Duplicate hits are annoying. A message that cannot be found is a broken feature.
Quote stripping is a heuristic, so every design choice here is the cautious one:

* **Stripping never destroys anything.** ``email_body_text.text`` keeps the full
  message; the stripped copy lives beside it in ``text_stripped`` and only the
  *index* shrinks. A heuristic this module gets wrong can be re-run later
  against text that is still there.
* **Only unambiguous markers.** ``>``-prefixed lines, attribution lines
  (``On … wrote:`` and its German/French equivalents), and the Outlook
  ``-----Original Message-----`` divider. No semantic detection, no "this
  paragraph looks quoted".
* **A reply written *inside* a quoted block survives**, which is the case that
  makes this hard. Only the ``>``-prefixed lines are dropped, so interleaved
  answers between them are kept. Cutting everything after the first quote
  marker would be simpler and would lose them.
* **Forwarded mail is not touched.** ``--- Forwarded message ---`` introduces
  content that *is* the substance of the message; treating it like a quote
  would make the entire forward unfindable.
* **When the result looks wrong, the module refuses.** :func:`strip_quotes`
  returns ``None`` — meaning "index the original" — whenever stripping produced
  nothing substantive. A message that is *only* a quoted block stays findable
  by falling back rather than becoming an empty index entry.

Returning ``None`` rather than the original string is load-bearing: the caller
stores ``NULL``, and the generated tsvector column is
``to_tsvector('english', COALESCE(text_stripped, text))``. "Refused to strip"
and "stripped to nothing" therefore cannot be confused at the schema level.
"""

from __future__ import annotations

import re

# ``>`` at line start, after optional whitespace. Depth is irrelevant — ``>>>``
# is as quoted as ``>``.
_QUOTED_LINE = re.compile(r"^\s*>")

# Attribution lines introducing a quote. Only the line itself is dropped; the
# quoted body below it is handled by _QUOTED_LINE, so an interleaved reply is
# not swept up with it.
#
# Kept deliberately tight — an unanchored "on … wrote" would match ordinary
# prose like "on Tuesday she wrote:".
_ATTRIBUTION_PATTERNS = (
    # English: "On Mon, 4 Aug 2026 at 09:12, Alice <a@b> wrote:"
    re.compile(r"^\s*On\b.{0,200}\bwrote:\s*$", re.IGNORECASE),
    # German: "Am 04.08.2026 um 09:12 schrieb Alice:"
    re.compile(r"^\s*Am\b.{0,200}\bschrieb\b.{0,80}:\s*$", re.IGNORECASE),
    # French: "Le 4 août 2026 à 09:12, Alice a écrit :"
    re.compile(r"^\s*Le\b.{0,200}\ba écrit\s*:\s*$", re.IGNORECASE),
)

# Dividers after which everything is a quoted copy of an earlier message. These
# are emitted by clients that do NOT ``>``-prefix, so the line-level rules above
# cannot catch them and a hard cut is the only option.
#
# ``--- Forwarded message ---`` is deliberately ABSENT: a forward's substance is
# the forwarded part, and cutting it would make the whole message unfindable.
_HARD_CUT_PATTERNS = (
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.IGNORECASE),
    re.compile(r"^\s*-{2,}\s*Ursprüngliche Nachricht\s*-{2,}\s*$", re.IGNORECASE),
)

_MIN_SUBSTANTIVE_CHARS = 8
"""Below this many alphanumeric characters, a stripped result is not trusted.

A reply of "Yes." is real and must stay findable, so the bar is deliberately
low — this exists to catch "stripping removed everything", not to judge
terseness.
"""


def _is_substantive(text: str) -> bool:
    """True when ``text`` carries enough real content to index on its own."""
    return sum(ch.isalnum() for ch in text) >= _MIN_SUBSTANTIVE_CHARS


def strip_quotes(text: str | None) -> str | None:
    """Return ``text`` without quoted reply history, or ``None`` to keep the original.

    ``None`` means "do not store a stripped copy" and is returned when:

    * ``text`` is empty;
    * no quote markers were found, so a stripped copy would duplicate the
      original for no benefit; or
    * stripping left nothing substantive — the whole-message-is-a-quote case,
      where indexing the original is the only way to keep it findable.

    Never raises: this runs on the ingest path, where a search index must not
    be able to break mail processing.
    """
    if not text:
        return None

    lines = text.splitlines()
    kept: list[str] = []
    found_marker = False

    for line in lines:
        if any(p.match(line) for p in _HARD_CUT_PATTERNS):
            found_marker = True
            break  # everything below is a quoted copy
        if _QUOTED_LINE.match(line):
            found_marker = True
            continue
        if any(p.match(line) for p in _ATTRIBUTION_PATTERNS):
            found_marker = True
            continue
        kept.append(line)

    if not found_marker:
        return None

    stripped = "\n".join(kept).strip()
    # Collapse the run of blank lines a removed quote block leaves behind.
    stripped = re.sub(r"\n{3,}", "\n\n", stripped)

    if not _is_substantive(stripped):
        return None

    if stripped == text.strip():
        return None

    return stripped
