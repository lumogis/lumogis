# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Numeric search over message text (LUM-827) — the query half.

The *index* half is a generated column, so its behaviour is asserted against
real Postgres in `test_migration_078_digits_index.py`, including the boundary
property the design rests on. Restating that here against a Python twin would
test the twin, not the column, and the two could diverge without either test
noticing.

What is left is genuinely Python: deciding whether a query is a numeric search
at all, and finding the number in the raw text so the snippet has something to
window over.
"""

from __future__ import annotations

from services.email_digits import find_digit_span
from services.email_digits import normalise_query

_NBSP = "\u00a0"  # thousands separator; a joiner, unlike a plain space


# ---------------------------------------------------------------------------
# What counts as a numeric search
# ---------------------------------------------------------------------------


def test_the_three_ways_of_writing_an_amount_are_one_query():
    """The entire user-visible point: these are one search to the person typing."""
    assert normalise_query("2400") == "2400"
    assert normalise_query("2,400") == "2400"
    assert normalise_query("2.400") == "2400"


def test_a_continental_amount_is_the_same_query():
    assert normalise_query(f"2{_NBSP}400,00") == "240000"


def test_a_query_with_letters_is_not_a_numeric_search():
    """`INV-2400` must not reach the digits index.

    Reducing it to `2400` would match every message mentioning 2400 in any
    context — the amount, a phone extension, a date — when the user asked
    about one specific reference. Mixed tokens stay with the ordinary text
    index, which already handles them correctly.
    """
    assert normalise_query("INV-2400") == ""
    assert normalise_query("order 2400") == ""


def test_a_query_holding_two_numbers_is_not_a_numeric_search():
    """Stripping every non-digit here would invert the index's own space rule.

    `1 2` would become `12`, which prefix-matches `124005` — a message
    containing neither 1 nor 2 as written — and `1000 1001` would become
    `10001001`, which can match nothing at all. Both ask the column for
    something it cannot mean.
    """
    assert normalise_query("1 2") == ""
    assert normalise_query("1000 1001") == ""


def test_an_amount_grouped_with_an_ordinary_space_is_refused():
    """`2 400` is genuinely ambiguous, and the index already read it as two.

    Answering it as `2400` would return amounts the message never contains;
    answering it as two numbers is not something a single prefix query can
    express. Refusing sends it to the text index, which is the honest answer.
    """
    assert normalise_query("2 400") == ""


def test_the_query_joins_by_the_same_rule_as_the_column():
    """The drift guard: query normalisation must mirror migration 078.

    `test_migration_078_digits_index.py` asserts the column's side of each of
    these. If one side gains or loses a separator, a query stops meaning what
    the tokens mean, and search fails in the direction users cannot see.
    """
    joiners = {"2,400": "2400", "2.400": "2400", f"2{_NBSP}400": "2400"}
    for written, expected in joiners.items():
        assert normalise_query(written) == expected, written

    # ...and the one character that must NOT join, on both sides.
    assert normalise_query("2 400") == ""


def test_a_single_digit_query_is_refused():
    """Symmetric with the index, which drops one-digit tokens.

    A `7` that matched every message is not a search, and answering it would
    only make the numeric result count meaningless.
    """
    assert normalise_query("7") == ""


def test_a_punctuation_only_query_is_refused():
    assert normalise_query("...") == ""
    assert normalise_query("  ") == ""
    assert normalise_query("") == ""
    assert normalise_query(None) == ""


def test_surrounding_whitespace_does_not_change_the_query():
    assert normalise_query("  2400 ") == "2400"


# ---------------------------------------------------------------------------
# Finding the number in the raw text, for the snippet
# ---------------------------------------------------------------------------


def test_the_snippet_can_locate_a_number_the_query_spells_differently():
    """Why this function exists at all.

    The index holds `240000`; the text the user will read says `2,400.00`. A
    plain substring search for `2400` finds nothing, so the hit would render
    with no excerpt — which the user cannot tell apart from a bad result.
    """
    text = "Total EUR 2,400.00 due in 30 days."

    at = find_digit_span(text, "2400")

    assert text[at:].startswith("2,400.00")


def test_it_matches_on_prefix_exactly_as_the_index_does():
    """It must not point at a number the index did not match.

    `2400` reaches "2,400.00" and must not reach `124005`, where the digits
    appear but not at the start.
    """
    assert find_digit_span("ref 124005", "2400") == -1


def test_an_ordinary_space_does_not_glue_two_numbers_together():
    """Mirrors the index: `2 400` is two numbers, `2<nbsp>400` is one.

    If this drifted from migration 078 the snippet would window over a number
    the index never matched — pointing the user at the wrong part of a message
    that legitimately matched somewhere else.
    """
    assert find_digit_span("2 400,00 EUR", "2400") == -1
    assert find_digit_span(f"2{_NBSP}400,00 EUR", "2400") == 0


def test_a_span_is_only_what_the_column_would_actually_join():
    """`12..345` is two tokens to Postgres, so it must not be one span here.

    A looser run pattern (`\\d[\\d.,]*`) swallows the doubled separator and
    reports a span of `12345` — a number the index never matched — so the
    snippet would highlight the wrong part of a message that matched elsewhere.
    """
    assert find_digit_span("ref 12..345", "12345") == -1


def test_no_number_and_empty_input_are_safe():
    assert find_digit_span("nothing numeric here", "2400") == -1
    assert find_digit_span("", "2400") == -1
    assert find_digit_span(None, "2400") == -1
    assert find_digit_span("2400", "") == -1
