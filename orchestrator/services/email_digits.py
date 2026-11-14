# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Numeric search over message text (LUM-827) — the query half.

A body containing ``EUR 2,400.00`` was not found by searching ``2400``:
Postgres tokenises it as ``2`` and ``400.00``, so no query token matches, and
ADR-211 § Consequences shipped that as a known gap. The same class covers
reference numbers, order IDs and account numbers — anything a human reads as
ONE token and a tokeniser splits.

**The index half is SQL, deliberately, and lives in migration 078.** The
normalisation is a pure regex, so a generated column can express it, and that
buys two things a Python-written column cannot: every message already stored
becomes searchable by number the moment the migration lands (no backfill — and
the ticket's complaint is precisely about mail you already have), and the
stored value cannot drift from the expression the query uses.

This module is therefore only what runs per *query*:

* :func:`normalise_query` — decide whether a query is a numeric search at all,
  and reduce it to the digits the ``:*`` prefix operator is given.
* :func:`find_digit_span` — locate the number in the raw text so the snippet
  can window over it. Without it a numeric hit renders with an empty snippet,
  because the raw text says ``2,400.00`` while the query says ``2400``.

The boundary property the whole design rests on — *digits from two different
numbers never end up in one token* — belongs to the generated column, so it is
asserted against real Postgres in ``tests/test_migration_078_digits_index.py``
rather than against a Python twin that could silently diverge from it.
"""

from __future__ import annotations

import re

_NON_DIGIT = re.compile(r"[^0-9]+")

#: Mirrors migration 078's join, character for character. Kept in step by
#: `test_the_query_joins_by_the_same_rule_as_the_column`; if the two drift, a
#: query stops meaning what the tokens mean.
_INNER_SEPARATOR = re.compile("(\\d)[.,\\u00a0\\u202f](\\d)")

#: One number as written. Each separator must sit BETWEEN digits, exactly as
#: migration 078's join requires — `\\d[\\d.,]*` would instead swallow `12..345`
#: and `12,` , spans the SQL never joins, and the caller would then window over
#: a number the index did not match.
#:
#: Ordinary whitespace is absent for the same reason it is absent in the
#: migration: two numbers separated by a space are the common case in prose,
#: and joining them manufactures matches. U+00A0 / U+202F are, between digits,
#: thousands separators and essentially nothing else.
_NUMERIC_RUN = re.compile("\\d+(?:[.,\\u00a0\\u202f]\\d+)*")

#: Anything that is neither a digit nor a character a number may carry. One
#: letter is enough to say "this is not a bare numeric search". Whitespace is
#: tolerated here so a multi-number query reaches the run count below, which
#: rejects it for a better-stated reason than "contains a letter".
_NOT_NUMERIC = re.compile("[^0-9.,\\u00a0\\u202f\\s]")

MIN_DIGITS = 2
"""Shortest query the digits index will answer.

Mirrors the one-digit tokens migration 078 drops (``\\m\\d\\M``): a ``7`` that
matched every message is not a search, and answering it would only make the
numeric result count meaningless.
"""


def normalise_query(query: str | None) -> str:
    """Return the digits of ``query``, or ``""`` when it is not a numeric search.

    ``2400``, ``2,400`` and ``2.400`` all reduce to ``2400``, which is what
    makes them one search to the person typing them — the entire user-visible
    point of the feature.

    A query containing **letters returns** ``""``. Reducing ``INV-2400`` to
    ``2400`` would match every message mentioning 2400 in any context — the
    amount, a phone extension, a date — when the user asked about one specific
    reference. Mixed tokens are left to the ordinary text index, which already
    handles them correctly. A deliberate scope limit, not an oversight.

    **A query holding more than one number also returns** ``""``, and the run
    count below is the only thing enforcing it. Stripping every non-digit
    instead would turn ``1 2`` into ``12`` — which prefix-matches ``124005``,
    a message containing neither 1 nor 2 as written — and ``1000 1001`` into
    ``10001001``, which can match nothing at all. Both invert the space rule
    the index applies, so the query would be asking for something the column
    cannot mean. ``2 400`` is refused for the same reason: it is genuinely
    ambiguous between one number and two, and the index has already committed
    to reading it as two.
    """
    if not query:
        return ""
    q = query.strip()
    if not q or _NOT_NUMERIC.search(q):
        return ""
    # Join separators BETWEEN digits, exactly as migration 078 does, so the
    # query is normalised by the same rule as the tokens it will be matched
    # against. Two passes for the same reason: a match consumes the digits
    # around it, so one pass leaves the second separator in `1,234,567`.
    joined = q
    for _ in range(2):
        joined = _INNER_SEPARATOR.sub(r"\1\2", joined)
    runs = [r for r in _NON_DIGIT.split(joined) if r]
    if len(runs) != 1:
        return ""
    return runs[0] if len(runs[0]) >= MIN_DIGITS else ""


def find_digit_span(text: str | None, digits: str) -> int:
    """Offset of the first number in ``text`` whose digits start with ``digits``.

    ``-1`` when there is none. The snippet needs this because the two sides
    spell the number differently: the index holds ``240000`` while the raw text
    the user will read says ``2,400.00``, so a plain substring search for the
    query finds nothing and the hit renders with no excerpt at all — which the
    user cannot tell apart from a bad result.

    Matching is **prefix**, exactly as the SQL is, so this cannot point at a
    number the index did not match.
    """
    if not text or not digits:
        return -1
    for m in _NUMERIC_RUN.finditer(text):
        if _NON_DIGIT.sub("", m.group(0)).startswith(digits):
            return m.start()
    return -1
