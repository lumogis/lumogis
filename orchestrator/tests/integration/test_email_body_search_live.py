# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Email search matching semantics against real Postgres (LUM-819).

These cases **cannot** live in the unit lane. `tsvector`, `gin_trgm_ops`,
`ILIKE … ESCAPE` and the `42702` ambiguity are behaviours of the database, and
the repo's fake store string-matches SQL rather than evaluating it — so a
fake-backed test of matching or ranking asserts on SQL *text* and passes
happily against SQL that returns the wrong rows.

What is pinned here is the decision itself. `'english'` **plus** the `:*`
prefix operator was chosen over `'simple'` because it is the only combination
that handles `invoice`↔"invoices", `plumb`→"plumbing" and — the case plain
`'english'` fails — `Rechnung`→"Rechnungen". If someone later drops the prefix,
or flips the config, the German case is what breaks first and it breaks here.

The config is baked into a ``GENERATED … STORED`` column, so changing it is a
full re-index of the corpus. That is why it is worth a test rather than a
comment.
"""

from __future__ import annotations

import os
import uuid

import pytest
from tests.integration._pgguard import require_postgres

psycopg2 = pytest.importorskip("psycopg2")

_MIGRATIONS = (
    "postgres/migrations/074-email-body-text-search.sql",
    # LUM-826 — the shipping search query selects COALESCE(text_stripped, text),
    # so a schema carrying only 074 no longer matches what ships and every query
    # here fails with `column ebt.text_stripped does not exist`. That failure is
    # this lane working as designed: it is the only place the real SQL meets a
    # real driver, and it caught the query and the schema drifting apart.
    # Anything that changes the columns email_search reads belongs in this tuple.
    "postgres/migrations/075-email-body-text-stripped.sql",
    # LUM-827 — adds `digits_tsv`, which the body arm ORs in for a bare numeric
    # query. Same rule as above: the query reads the column, so the schema here
    # must carry it or the numeric search 500s against a real driver.
    "postgres/migrations/078-email-body-digits.sql",
)

_SIGNALS_DDL = """
CREATE TABLE signals (
    signal_id        UUID PRIMARY KEY,
    user_id          TEXT NOT NULL DEFAULT 'default',
    source_id        TEXT NOT NULL DEFAULT '',
    title            TEXT NOT NULL DEFAULT '',
    url              TEXT NOT NULL DEFAULT '',
    content_summary  TEXT NOT NULL DEFAULT '',
    from_addr        TEXT NOT NULL DEFAULT '',
    from_display     TEXT NOT NULL DEFAULT '',
    scope            TEXT NOT NULL DEFAULT 'personal',
    published_from   UUID REFERENCES signals(signal_id) ON DELETE CASCADE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _conn_kwargs() -> dict:
    return {
        "host": os.environ.get("POSTGRES_HOST", "postgres"),
        "port": int(os.environ.get("POSTGRES_PORT", "5432")),
        "user": os.environ.get("POSTGRES_USER", "lumogis"),
        "password": os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
        "dbname": os.environ.get("POSTGRES_DB", "lumogis"),
        "connect_timeout": 3,
    }


def _migration_sql() -> list[str]:
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    return [(root / m).read_text(encoding="utf-8") for m in _MIGRATIONS]


@pytest.fixture
def db():
    """A throwaway schema carrying migrations 074 + 075 exactly as they ship."""
    schema = f"lum819_{uuid.uuid4().hex[:8]}"
    try:
        conn = psycopg2.connect(**_conn_kwargs())
    except Exception as exc:  # pragma: no cover - environment dependent
        require_postgres(exc)
        raise
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(f"SET search_path TO {schema}, public")
        cur.execute(_SIGNALS_DDL)
        # The migrations verbatim, in order — no reformatting, or this tests a copy.
        for sql in _migration_sql():
            cur.execute(sql)
    try:
        yield conn, schema
    finally:
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA {schema} CASCADE")
        conn.close()


def _seed(conn, schema: str) -> None:
    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {schema}, public")
        for sid, title, body in (
            (
                "11111111-1111-1111-1111-111111111111",
                "Plumbing quote",
                "Please find our invoices for the plumbing work, EUR 2,400.00 due in 30 days.",
            ),
            (
                "22222222-2222-2222-2222-222222222222",
                "Angebot",
                "Rechnungen fuer die Sanitaerarbeiten von Müller Sanitär GmbH.",
            ),
        ):
            cur.execute(
                # user_id matters: visible_filter binds the CALLER, so a row left
                # on the 'default' owner is invisible to the user under test and
                # the search tests would pass for the wrong reason.
                "INSERT INTO signals (signal_id, user_id, title, url) VALUES (%s, %s, %s, %s)",
                (sid, "u1", title, f"imap://src/100/{sid[:1]}"),
            )
            cur.execute(
                "INSERT INTO email_body_text (signal_id, user_id, text) VALUES (%s, %s, %s)",
                (sid, "u1", body),
            )


def _body_matches(conn, schema: str, tsquery: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {schema}, public")
        cur.execute(
            "SELECT count(*) FROM email_body_text WHERE text_tsv @@ to_tsquery('english', %s)",
            (tsquery,),
        )
        return int(cur.fetchone()[0])


@pytest.mark.parametrize(
    "query,expected,why",
    [
        ("invoice:*", 1, "stemming: the body says 'invoices'"),
        ("invoices:*", 1, "and the reverse direction"),
        ("plumb:*", 1, "prefix: the body says 'plumbing'"),
        ("Rechnung:*", 1, "THE German case — plain 'english' misses this"),
        ("Müller:*", 1, "non-ASCII letters survive the sanitiser"),
        ("nothingatall:*", 0, "a genuine miss is still a miss"),
    ],
)
def test_the_chosen_config_matches_what_it_was_chosen_for(db, query, expected, why):
    conn, schema = db
    _seed(conn, schema)

    assert _body_matches(conn, schema, query) == expected, why


def test_the_generated_column_populates_without_being_written(db):
    """It is ``GENERATED ALWAYS … STORED``; nothing inserts ``text_tsv``."""
    conn, schema = db
    _seed(conn, schema)

    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {schema}, public")
        cur.execute("SELECT count(*) FROM email_body_text WHERE text_tsv IS NOT NULL")
        assert int(cur.fetchone()[0]) == 2


def test_deleting_a_signal_takes_its_body_and_dead_rows_with_it(db):
    """``ON DELETE CASCADE`` is what makes erasure mechanical rather than a sweep."""
    conn, schema = db
    _seed(conn, schema)
    sid = "11111111-1111-1111-1111-111111111111"

    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {schema}, public")
        cur.execute(
            "INSERT INTO email_body_text_dead (signal_id, reason) VALUES (%s, 'not_found')",
            (sid,),
        )
        cur.execute("DELETE FROM signals WHERE signal_id = %s", (sid,))
        cur.execute("SELECT count(*) FROM email_body_text WHERE signal_id = %s", (sid,))
        assert int(cur.fetchone()[0]) == 0
        cur.execute("SELECT count(*) FROM email_body_text_dead WHERE signal_id = %s", (sid,))
        assert int(cur.fetchone()[0]) == 0


def test_the_migration_applies_twice_cleanly(db):
    """The runner is filename-tracked, but a re-run must not be destructive.

    LUM-826 raises the stakes here: 075 ``DROP COLUMN IF EXISTS text_tsv`` and
    re-adds it, because a generated expression cannot be altered in place. A
    re-run therefore rebuilds the vector and its GIN index — the stored ``text``
    and ``text_stripped`` must survive that, and the index must come back
    usable rather than merely present.
    """
    conn, schema = db
    _seed(conn, schema)

    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {schema}, public")
        for sql in _migration_sql():
            cur.execute(sql)
        cur.execute("SELECT count(*) FROM email_body_text")
        assert int(cur.fetchone()[0]) == 2, "re-applying must not drop stored text"
        # The regenerated column must still answer queries, not just exist.
        cur.execute("SELECT count(*) FROM email_body_text WHERE text_tsv IS NOT NULL")
        assert int(cur.fetchone()[0]) == 2, "the regenerated vector must repopulate"


def test_an_amount_with_separators_is_not_found_by_the_WORD_index(db):
    """The gap is real; it is just no longer the whole story (LUM-827).

    This replaces the pinned-gap test that shipped with LUM-819, whose
    docstring said to delete it once a digits-only copy existed. Migration 078
    is that copy — but the reason the gap needed a separate mechanism has not
    changed, and asserting it here is what stops someone concluding the word
    index grew the ability and removing the numeric one. The positive case
    lives in `test_a_bare_number_finds_an_amount_written_with_separators`.
    """
    conn, schema = db
    _seed(conn, schema)

    assert _body_matches(conn, schema, "2400:*") == 0, (
        "'EUR 2,400.00' still tokenises as '2' and '400.00' under 'english'"
    )


# ---------------------------------------------------------------------------
# The service against a real driver
# ---------------------------------------------------------------------------
#
# These exist because the unit lane CANNOT catch a whole class of defect: the
# fake store returns canned rows without ever handing the SQL to psycopg2, so a
# statement that the DRIVER rejects looks perfectly healthy there. That is
# exactly how `url LIKE 'imap://%'` shipped — psycopg2 %-formats the query
# before Postgres sees it, so a lone `%` in a statement executed with a params
# tuple raises `IndexError` and every single search 500s. Seventy-one green
# unit tests said nothing about it.


class _RealStore:
    """Minimal metadata-store port over a real psycopg2 connection."""

    def __init__(self, conn, schema: str) -> None:
        self._conn = conn
        self._schema = schema

    def _cursor(self):
        from psycopg2.extras import RealDictCursor

        cur = self._conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(f"SET search_path TO {self._schema}, public")
        return cur

    def fetch_all(self, sql, params=None):
        with self._cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def fetch_one(self, sql, params=None):
        with self._cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None

    def execute(self, sql, params=None):
        with self._cursor() as cur:
            cur.execute(sql, params)


@pytest.fixture
def real_search(db, monkeypatch):
    """`search_email` wired to a real connection, with the full signals shape."""
    from visibility import UserContext

    import config

    conn, schema = db
    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {schema}, public")
        # build_signals_query selects the whole GET /signals column set and
        # joins signal_triage, so the throwaway schema needs both.
        for col, ddl in (
            ("published_at", "TIMESTAMPTZ"),
            ("entities", "JSONB NOT NULL DEFAULT '[]'"),
            ("topics", "JSONB NOT NULL DEFAULT '[]'"),
            ("importance_score", "FLOAT NOT NULL DEFAULT 0"),
            ("relevance_score", "FLOAT NOT NULL DEFAULT 0"),
            ("notified", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ("category", "TEXT NOT NULL DEFAULT ''"),
            ("source_url", "TEXT"),
            ("source_label", "TEXT"),
            ("email_message_id", "TEXT NOT NULL DEFAULT ''"),
        ):
            cur.execute(f"ALTER TABLE signals ADD COLUMN IF NOT EXISTS {col} {ddl}")
        cur.execute(
            "CREATE TABLE IF NOT EXISTS signal_triage ("
            " user_id TEXT NOT NULL, signal_id UUID NOT NULL,"
            " status TEXT NOT NULL, snoozed_until TIMESTAMPTZ,"
            " updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
            " PRIMARY KEY (user_id, signal_id))"
        )
    _seed(conn, schema)

    store = _RealStore(conn, schema)
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)
    return UserContext(user_id="u1", role="member", allows_shared=True)


def test_search_executes_against_a_real_driver(real_search):
    """The regression test for the literal-`%` defect. It must simply not raise."""
    from services import email_search

    result = email_search.search_email(real_search, "plumbing")

    assert isinstance(result["hits"], list)
    assert "counts" in result


def test_a_body_only_phrase_finds_its_message(real_search):
    """The feature's whole promise, end to end through real SQL."""
    from services import email_search

    hits = email_search.search_email(real_search, "plumbing")["hits"]

    assert [h["title"] for h in hits] == ["Plumbing quote"]
    assert "body" in hits[0]["matched_in"]


def test_a_bare_number_finds_an_amount_written_with_separators(real_search):
    """LUM-827, end to end through the real SQL the service builds.

    The seeded body says "EUR 2,400.00"; the user types "2400". This is the
    single behaviour the ticket asked for, and it exercises the arm the unit
    lane cannot: `digits_tsv` OR-ed into the body predicate, bound in the right
    placeholder position, against a real driver.
    """
    from services import email_search

    result = email_search.search_email(real_search, "2400")

    assert [h["title"] for h in result["hits"]] == ["Plumbing quote"]
    assert "body" in result["hits"][0]["matched_in"]
    assert result["counts"]["body"] == 1


def test_a_numeric_hit_still_gets_a_snippet(real_search):
    """Without `find_digit_span` this is empty on exactly the new hits.

    The index matched `240000` while the text says "2,400.00", so the snippet's
    substring pass finds nothing — and a hit rendered with no excerpt is one
    the user cannot tell apart from a bad result.
    """
    from services import email_search

    hit = email_search.search_email(real_search, "2400")["hits"][0]

    assert "2,400.00" in hit["snippet"]


def test_a_number_with_letters_does_not_run_the_numeric_index(real_search):
    """`INV-2400` is a reference, not an amount — deliberate scope limit.

    Were it reduced to `2400` it would return this message, whose body mentions
    2400 only as a sum. The ordinary text index answers it instead, correctly
    finding nothing.
    """
    from services import email_search

    assert email_search.search_email(real_search, "INV-2400")["hits"] == []


def test_counts_are_reported_per_field(real_search):
    from services import email_search

    counts = email_search.search_email(real_search, "plumbing")["counts"]

    assert counts["all"] == 1
    assert counts["body"] == 1
    assert counts["sender"] == 0


def test_every_field_selector_executes(real_search):
    """Each `fields` value builds different SQL; all four must be valid."""
    from services import email_search

    for fields in ("all", "subject", "sender", "body"):
        result = email_search.search_email(real_search, "plumbing", fields=fields)
        assert isinstance(result["hits"], list), fields


def test_a_hostile_query_does_not_reach_postgres_as_syntax(real_search):
    """What the sanitiser buys, proven against the real parser."""
    from services import email_search

    for hostile in ["foo &", "a |", '"unbalanced', "&&", "x'); DROP TABLE signals;--"]:
        # Two acceptable outcomes, and no third: the query is REFUSED before any
        # SQL runs, or it runs and returns rows. What must never happen is a
        # database error — that is the 500 reachable from a search box.
        try:
            result = email_search.search_email(real_search, hostile)
        except email_search.QueryTooShort:
            continue
        assert isinstance(result["hits"], list), hostile

    # And the table is still there.
    assert email_search.search_email(real_search, "plumbing")["counts"]["all"] == 1


def test_a_non_email_signal_never_appears_in_email_search(real_search, db):
    """`url LIKE 'imap://%%'` — the predicate whose escaping was the bug."""
    from services import email_search

    conn, schema = db
    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {schema}, public")
        cur.execute(
            "INSERT INTO signals (signal_id, user_id, title, url) "
            "VALUES ('33333333-3333-3333-3333-333333333333', 'u1',"
            " 'Plumbing news article', 'https://example.com/plumbing')"
        )

    titles = [h["title"] for h in email_search.search_email(real_search, "plumbing")["hits"]]

    assert titles == ["Plumbing quote"], "an RSS signal leaked into email search"
