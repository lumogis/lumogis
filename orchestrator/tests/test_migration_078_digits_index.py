# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Migration 078 makes `2400` find "EUR 2,400.00" (LUM-827).

The normalisation IS the generated column, so this is where it is tested. A
Python twin asserting the same properties would be testing the twin: the two
could diverge — a regex flag, a Postgres ARE dialect difference, an escape that
means something else in a SQL literal — and both test suites would stay green
while search quietly broke.

Two things are being proved, and the second matters more than the first:

* **`2400` finds "EUR 2,400.00"** — the ticket, and the ADR-211 § Consequences
  bullet this migration retires.
* **Digits from different numbers never merge** — the property that makes the
  first one safe. The ticket proposed stripping every non-digit from the whole
  document; measured, that manufactures matches that appear in none of the
  numbers involved, and a search returning confident wrong answers is worse
  than one returning none.

Skips when no Postgres is reachable, like the other migration regressions here.
Runs against a throwaway database because it adds a generated column.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MIGRATIONS = _REPO_ROOT / "postgres" / "migrations"

_SIGNALS_STUB = """
CREATE TABLE signals (
    signal_id UUID PRIMARY KEY,
    user_id TEXT,
    title TEXT,
    from_addr TEXT,
    from_display TEXT,
    content_summary TEXT
)
"""

#: The document that killed the ticket's proposal. Three unrelated numbers, one
#: of them a bare `00`, in the order a real message would carry them. Stripping
#: every non-digit yields `077009000240024000030`, which contains `90002400` —
#: present in NEITHER the phone number nor the invoice.
_MIXED = "Call me on 07700 900024. Invoice 00 attached. Total EUR 2,400.00 due in 30 days."

_NBSP = "\u00a0"  # thousands separator; a joiner, unlike a plain space


def _admin_kwargs(dbname: str | None = None) -> dict:
    return {
        "host": os.environ.get("POSTGRES_HOST", "postgres"),
        "port": int(os.environ.get("POSTGRES_PORT", "5432")),
        "user": os.environ.get("POSTGRES_USER", "lumogis"),
        "password": os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
        "dbname": dbname or os.environ.get("POSTGRES_DB", "lumogis"),
        "connect_timeout": 3,
    }


_FILES = (
    "074-email-body-text-search.sql",
    "075-email-body-text-stripped.sql",
    "078-email-body-digits.sql",
)


@pytest.fixture(scope="module")
def migrated_db():
    """A throwaway database with 074 + 075 + 078 applied. Yields a connection."""
    try:
        admin = psycopg2.connect(**_admin_kwargs())
    except Exception as exc:  # noqa: BLE001 — diagnostic skip
        pytest.skip(f"Postgres not reachable for migration 078 test: {exc}")
    admin.autocommit = True
    name = f"t078_{uuid.uuid4().hex[:12]}"
    try:
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{name}"')
    except Exception as exc:  # noqa: BLE001
        admin.close()
        pytest.skip(f"cannot create a test database (needs CREATE): {exc}")

    conn = psycopg2.connect(**_admin_kwargs(name))
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(_SIGNALS_STUB)
            for f in _FILES:
                cur.execute((_MIGRATIONS / f).read_text(encoding="utf-8"))
        yield conn
    finally:
        conn.close()
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def _store(conn, text: str, stripped: str | None = None) -> str:
    """Insert one message, returning a user id nothing else uses."""
    user = f"u_{uuid.uuid4().hex[:8]}"
    sid = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute("INSERT INTO signals (signal_id, user_id) VALUES (%s, %s)", (sid, user))
        cur.execute(
            "INSERT INTO email_body_text (signal_id, user_id, text, text_stripped) "
            "VALUES (%s, %s, %s, %s)",
            (sid, user, text, stripped),
        )
    return user


def _finds(conn, user: str, digits: str) -> int:
    """Rows this user owns that the digits index matches, prefix-wise."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM email_body_text "
            "WHERE user_id = %s AND digits_tsv @@ to_tsquery('simple', %s)",
            (user, f"{digits}:*"),
        )
        return int(cur.fetchone()[0])


def _tokens(conn, user: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT array_agg(lexeme) FROM email_body_text, "
            "LATERAL unnest(digits_tsv) WHERE user_id = %s",
            (user,),
        )
        return sorted(cur.fetchone()[0] or [])


# ---------------------------------------------------------------------------
# The ticket
# ---------------------------------------------------------------------------


def test_2400_finds_an_amount_written_with_separators(migrated_db):
    """The headline, and the ADR-211 § Consequences bullet this retires."""
    user = _store(migrated_db, "Total EUR 2,400.00 due in 30 days.")

    assert _finds(migrated_db, user, "2400") == 1


def test_the_same_amount_written_three_ways_is_all_findable(migrated_db):
    for text in ("EUR 2400", "EUR 2,400", "EUR 2.400", f"EUR 2{_NBSP}400,00"):
        user = _store(migrated_db, text)
        assert _finds(migrated_db, user, "2400") == 1, text


def test_a_reference_number_is_findable_too(migrated_db):
    """Same class as the amount: one token to a human, several to a tokeniser."""
    user = _store(migrated_db, "Your order 1,234,567 has shipped.")

    assert _finds(migrated_db, user, "1234567") == 1


# ---------------------------------------------------------------------------
# The property that makes it safe
# ---------------------------------------------------------------------------


def test_digits_from_different_numbers_never_share_a_token(migrated_db):
    """The measurement that rejected the ticket's whole-document strip.

    `90002400` spans the end of the phone number and the start of the invoice.
    It exists only if the two are joined, and every such artefact would be a
    silent false positive.
    """
    user = _store(migrated_db, _MIXED)

    assert _finds(migrated_db, user, "90002400") == 0
    assert not any("90002400" in t for t in _tokens(migrated_db, user))


def test_an_ordinary_space_does_not_join_two_numbers(migrated_db):
    """A list of references must stay a list, not become one long number.

    With `\\s` in the joining class — the shape tried first — "1000 1001 1002"
    became the single token `100010011002`. Two numbers separated by a space
    are the common case in prose; a space inside one number is the rare one.
    """
    user = _store(migrated_db, "See references 1000 1001 1002.")

    assert _tokens(migrated_db, user) == ["1000", "1001", "1002"]


def test_a_comma_list_written_with_spaces_stays_a_list(migrated_db):
    """The ordinary way to write a list never reaches the joining pass.

    The join requires a digit *immediately* after the separator, so `12, 13`
    is two numbers and each stays findable. This is what keeps the ambiguity
    below narrow rather than pervasive.
    """
    user = _store(migrated_db, "Covering weeks 12, 13, 14.")

    assert _tokens(migrated_db, user) == ["12", "13", "14"]


def test_a_comma_list_without_spaces_reads_as_one_number(migrated_db):
    """The one ambiguity passes 1+2 cannot resolve — pinned as a decision.

    `12,13` is a German or French decimal (twelve point one three) *and* a
    space-less English list. This reads it as one number, so `weeks 12,13,14`
    indexes as `121314`: searching `13` misses it and `1213` finds it.

    Chosen, not overlooked. Lumogis indexes German mail as a first concern
    (ADR-211 exists largely for that), where the decimal reading is simply
    correct; and the list written the ordinary way, with spaces, is covered
    above. Change this and a German amount stops being one number.
    """
    user = _store(migrated_db, "Covering weeks 12,13,14.")

    assert _tokens(migrated_db, user) == ["121314"]
    assert _finds(migrated_db, user, "13") == 0


def test_a_german_decimal_is_one_number(migrated_db):
    """The reading the rule above is chosen for: `12,13 EUR` is 12.13."""
    user = _store(migrated_db, "Betrag 12,13 EUR")

    assert _tokens(migrated_db, user) == ["1213"]


def test_a_line_break_does_not_join_two_numbers(migrated_db):
    """Wrapped statements are the common shape; `12\\n34` is not 1234."""
    user = _store(migrated_db, "12\n34")

    assert _tokens(migrated_db, user) == ["12", "34"]


def test_the_digits_must_be_at_the_start_of_the_number(migrated_db):
    """Prefix, not substring: `2400` must not reach `124005`.

    Substring matching would catch it. Prefix does not, and that is what keeps
    the false-positive rate honest while still finding the amount somebody
    actually typed.
    """
    user = _store(migrated_db, "internal ref 124005")

    assert _finds(migrated_db, user, "2400") == 0


def test_single_digits_are_not_indexed(migrated_db):
    """Every message has a `1` somewhere; indexing them helps nobody."""
    user = _store(migrated_db, "option 1 or option 2, ref 34")

    assert _tokens(migrated_db, user) == ["34"]


# ---------------------------------------------------------------------------
# Staying in step with the rest of the schema
# ---------------------------------------------------------------------------


def test_a_number_only_in_the_quoted_history_is_not_indexed(migrated_db):
    """The digits column reads the same COALESCE as `text_tsv` (LUM-826).

    If it read `text` instead, an amount quoted in every reply would be
    findable once per reply — exactly the amplification migration 075 removed
    from the word index, reintroduced through the numeric one.
    """
    user = _store(migrated_db, "> Total EUR 2,400.00 from last week", "Thanks, noted.")

    assert _finds(migrated_db, user, "2400") == 0


def test_a_message_with_no_numbers_indexes_nothing(migrated_db):
    """An empty vector simply never matches — no special case downstream."""
    user = _store(migrated_db, "No numbers here at all.")

    with migrated_db.cursor() as cur:
        cur.execute(
            "SELECT digits_tsv::text FROM email_body_text WHERE user_id = %s",
            (user,),
        )
        assert cur.fetchone()[0] == ""


def test_the_migration_applies_twice_cleanly(migrated_db):
    """The runner is filename-tracked, but a re-run must not be destructive."""
    user = _store(migrated_db, "Total EUR 2,400.00")

    with migrated_db.cursor() as cur:
        cur.execute((_MIGRATIONS / "078-email-body-digits.sql").read_text(encoding="utf-8"))

    assert _finds(migrated_db, user, "2400") == 1
