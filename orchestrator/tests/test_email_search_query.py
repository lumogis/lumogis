# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Query normalisation and SQL shape for email search (LUM-819).

Prefix matching (``:*``) cannot be expressed through ``websearch_to_tsquery``,
so this feature builds its own ``tsquery`` and takes back the injection risk
that function existed to remove. **The sanitiser is therefore a security
control, not a formatting step** — every ``tsquery`` metacharacter is a
non-word character, so a token that has passed it cannot contain one.

Two findings measured on Postgres 16 are pinned here:

* A naive ``[A-Za-z0-9]`` strip turns ``Müller`` into ``Mller`` and returns
  **zero** rows against a body containing "Müller Sanitär GmbH" — searching for
  a German supplier by name would fail silently, forever.
* A punctuation-only query sanitises to ``""``, and ``to_tsquery('english','')``
  does **not** raise: it matches nothing and emits only a NOTICE. Without an
  explicit guard the failure is silent, which is the exact failure mode this
  feature exists to remove.
"""

from __future__ import annotations

import pytest

from services import email_search

# ---------------------------------------------------------------------------
# The sanitiser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("invoice", "invoice:*"),
        ("the invoice", "the:* & invoice:*"),
        ("  spaced   out  ", "spaced:* & out:*"),
        # Non-ASCII letters MUST survive — this is the German-search regression.
        ("Müller Sanitär", "Müller:* & Sanitär:*"),
        ("Straße 12", "Straße:* & 12:*"),
        # Every tsquery metacharacter is stripped.
        ("foo & bar", "foo:* & bar:*"),
        ("a:*|b", "ab:*"),
        ("x'); DROP TABLE b;--", "x:* & DROP:* & TABLE:* & b:*"),
        ("!!! &&& |||", ""),
        ("", ""),
    ],
)
def test_prefix_tsquery_is_built_safely(raw, expected):
    assert email_search.to_prefix_tsquery(raw) == expected


def test_the_sanitiser_preserves_non_ascii_letters():
    """The measured regression: an ASCII-only class breaks German search.

    Mutation check: narrow ``_TSQUERY_STRIP`` to ``[^A-Za-z0-9]`` and this fails.
    """
    assert email_search.to_prefix_tsquery("Müller") == "Müller:*"


@pytest.mark.parametrize(
    "hostile",
    ["foo &", "a |", '"unbalanced', "&&", ":*", "x'); DROP TABLE b;--", "((()))", "\\\\"],
)
def test_no_tsquery_metacharacter_survives_sanitisation(hostile):
    """Mutation check: make ``_TSQUERY_STRIP`` a no-op and this fails."""
    out = email_search.to_prefix_tsquery(hostile)
    for meta in "&|!()<>:'\\\"":
        assert meta not in out.replace(":*", "").replace(" & ", ""), f"{meta!r} survived"


# ---------------------------------------------------------------------------
# ILIKE escaping — a different normalisation, deliberately
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("plumber", "%plumber%"),
        ("100%", r"%100\%%"),  # the inner % is escaped; the wrappers are not
        ("a_b", r"%a\_b%"),
        (r"back\slash", r"%back\\slash%"),
    ],
)
def test_like_pattern_escapes_wildcards(raw, expected):
    assert email_search.to_like_pattern(raw) == expected


def test_like_escaping_orders_backslash_first():
    """Escaping ``%`` before ``\\`` would double-escape and corrupt the pattern."""
    assert email_search.to_like_pattern(r"50\%") == r"%50\\\%%"


# ---------------------------------------------------------------------------
# Rejection before any SQL runs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["ab", "a", "", "   "])
def test_query_shorter_than_the_minimum_is_refused(bad):
    with pytest.raises(email_search.QueryTooShort):
        email_search.normalise_query(bad)


def test_a_punctuation_only_query_is_refused():
    """Survives the length guard, sanitises to empty, and would match nothing.

    ``to_tsquery('english','')`` does not raise, so without this guard the user
    gets a silent empty list instead of being told the query is unusable.
    """
    with pytest.raises(email_search.QueryTooShort):
        email_search.normalise_query("!!!! &&&&")


def test_a_valid_query_normalises_to_both_forms():
    norm = email_search.normalise_query("Müller invoice")
    assert norm.tsquery == "Müller:* & invoice:*"
    assert norm.like == "%Müller invoice%"


# ---------------------------------------------------------------------------
# Field scoping — which arms are emitted
# ---------------------------------------------------------------------------


def test_body_only_search_emits_no_ilike_arms():
    sql, _ = email_search.build_match_predicate(
        email_search.normalise_query("invoice"), fields="body"
    )
    assert "text_tsv" in sql
    assert "ILIKE" not in sql.upper()


def test_subject_only_search_emits_no_body_arm():
    sql, _ = email_search.build_match_predicate(
        email_search.normalise_query("invoice"), fields="subject"
    )
    assert "text_tsv" not in sql
    assert "title ILIKE" in sql


def test_sender_only_search_covers_both_sender_columns():
    sql, _ = email_search.build_match_predicate(
        email_search.normalise_query("plumber"), fields="sender"
    )
    assert "from_addr ILIKE" in sql and "from_display ILIKE" in sql
    assert "title ILIKE" not in sql


def test_all_fields_search_emits_every_arm():
    sql, params = email_search.build_match_predicate(
        email_search.normalise_query("invoice"), fields="all"
    )
    for arm in (
        "title ILIKE",
        "from_addr ILIKE",
        "from_display ILIKE",
        "content_summary ILIKE",
        "text_tsv",
    ):
        assert arm in sql, arm
    assert len(params) == 5, "four ILIKE params plus one tsquery"


def test_every_ilike_arm_states_its_escape_clause():
    sql, _ = email_search.build_match_predicate(
        email_search.normalise_query("invoice"), fields="all"
    )
    assert sql.upper().count("ESCAPE") == sql.upper().count("ILIKE")


# ---------------------------------------------------------------------------
# Placeholder ordering and scope — the bugs no predicate-text test would catch
# ---------------------------------------------------------------------------


class _CapturingStore:
    """Captures the SQL and params the service actually binds."""

    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def fetch_all(self, sql, params=None):
        self.calls.append((sql, tuple(params or ())))
        return []

    def fetch_one(self, sql, params=None):
        self.calls.append((sql, tuple(params or ())))
        return {}

    def execute(self, sql, params=None):
        return None

    def main_query(self) -> tuple[str, tuple]:
        return next((c for c in self.calls if " FROM signals " in c[0] and "WITH m" not in c[0]))


@pytest.fixture
def captured(monkeypatch):
    from visibility import UserContext

    import config

    store = _CapturingStore()
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)
    user = UserContext(user_id="alice", role="member", allows_shared=True)
    return store, user


def test_placeholder_order_binds_the_caller_before_the_predicates(captured):
    store, user = captured
    email_search.search_email(user, "invoice")

    sql, params = store.main_query()
    # 1: body-match tsquery (SELECT), 2: triage LATERAL, 3: body LATERAL.
    assert params[0] == "invoice:*"
    assert params[1] == "alice", "triage LATERAL binds the caller FIRST of the two"
    assert params[2] == "alice", "body LATERAL binds the caller SECOND"
    assert sql.count("%s") == len(params), "every placeholder is bound exactly once"


def test_the_body_lateral_is_owner_scoped_not_household_scoped(captured):
    """The security decision: subject/sender use the union, body does not.

    Mutation check: swap ``authored_by_filter`` for ``visible_filter`` in
    ``_body_lateral`` and this fails.
    """
    store, user = captured
    email_search.search_email(user, "invoice")

    sql, _ = store.main_query()
    lateral = sql[sql.index("email_body_text") : sql.index(") b ON TRUE")]
    assert "signals.scope IN ('personal', 'shared')" in lateral
    assert "signals.user_id = %s" in lateral
    assert "'system'" not in lateral, "the household union must not reach message text"


def test_the_body_lateral_qualifies_its_columns(captured):
    """Unqualified columns here raise 42702 — both tables carry user_id."""
    store, user = captured
    email_search.search_email(user, "invoice")

    sql, _ = store.main_query()
    lateral = sql[sql.index("email_body_text") : sql.index(") b ON TRUE")]
    assert "signals.user_id" in lateral
    assert " user_id = %s" not in lateral.replace("signals.user_id = %s", "")


def test_search_is_scoped_to_email_signals_only(captured):
    """Without this an RSS signal whose title matched appears in EMAIL search."""
    store, user = captured
    email_search.search_email(user, "invoice")

    sql, _ = store.main_query()
    # `%%`, not `%`: psycopg2 %-formats the query string before Postgres sees
    # it, so a lone `%` in a statement executed WITH a params tuple raises
    # IndexError and every search 500s. `routes/signals.py` sidesteps this by
    # binding "imap://%" as a VALUE; here it is a literal and must be doubled.
    assert "url LIKE 'imap://%%'" in sql


def test_an_unknown_fields_selector_is_rejected(captured):
    _, user = captured
    with pytest.raises(ValueError):
        email_search.search_email(user, "invoice", fields="everything")


# ---------------------------------------------------------------------------
# The snippet — which number it windows over (LUM-827)
# ---------------------------------------------------------------------------
#
# A wrong excerpt on a right result is worse than no excerpt: the user reads it
# as the reason the message matched, and quietly concludes the search is bad.


def _long_body() -> str:
    """A realistic body where the two candidate windows do not overlap.

    `124005` contains the substring `2400`; the amount the digits index actually
    matched sits several hundred characters later. A short body would hide the
    defect by fitting both into one window.
    """
    return (
        "Hi Alex, quoting for the loft conversion under our framework Ref 124005. "
        + "The scaffolding schedule and the party-wall notice are attached as before. " * 3
        + "Total EUR 2,400.00 due in 30 days. Thanks, Sam."
    )


def test_a_numeric_snippet_windows_over_the_number_the_index_matched():
    """The digits index is authoritative about where the number is.

    A plain `find("2400")` lands INSIDE `124005` — a number the index
    deliberately did not match, because matching is prefix and not substring.
    Anchoring there excerpts a reference the user did not search for and omits
    the amount they did.
    """
    nq = email_search.normalise_query("2400")

    snippet = email_search._snippet(_long_body(), nq)

    assert "2,400.00" in snippet
    assert "124005" not in snippet


def test_the_word_pass_still_answers_a_non_numeric_query():
    """`normalise_query` yields no digits for anything with a letter, so the
    ordinary substring pass is untouched by the numeric branch."""
    nq = email_search.normalise_query("invoice")
    assert nq.digits == ""

    assert "invoice" in email_search._snippet("Please review the invoice today.", nq)


def test_a_numeric_query_with_no_number_in_the_text_falls_back_rather_than_failing():
    """`find_digit_span` returning -1 must not swallow a hit the WORD index made.

    Both arms are OR-ed, so a body can match on `text_tsv` while carrying no
    numeric token the digits index would locate.
    """
    nq = email_search.normalise_query("2400")

    assert email_search._snippet("order 2400x shipped", nq) != ""
