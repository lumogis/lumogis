# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Recipient suggestions against real Postgres (LUM-818).

These cases **cannot** live in the unit lane, for the reason ADR-211 records
after LUM-819 shipped a `%`-formatting bug with 71 green unit tests: the repo's
fake store string-matches SQL rather than handing it to a driver, so a
fake-backed test passes happily against SQL that psycopg2 would refuse or that
Postgres would evaluate differently.

Three behaviours here are driver- or engine-level and nothing else guards them:

* **`LIKE` escaping.** `_escape_like` neutralises `%` and `_`. If it were wrong,
  a query of `%` would match every correspondent the user has — the enumeration
  the two-character floor exists to bound. The unit lane cannot tell: its fake
  does substring matching in Python.
* **`unnest(to_addrs || cc_addrs)`** is Postgres array behaviour. A fake that
  concatenates Python lists proves nothing about it.
* **BCC exclusion end to end.** The unit test asserts the string `bcc` is absent
  from the SQL. This asserts the stronger thing: a blind-copied address does not
  come back out of a real database.
"""

from __future__ import annotations

import os
import uuid

import pytest
from tests.integration._pgguard import require_postgres

psycopg2 = pytest.importorskip("psycopg2")
# Module level, NOT inside the fixture: a local `import psycopg2.extras` rebinds
# `psycopg2` as a local name for the whole function, so the connect() call above
# it fails with "cannot access local variable". Caught by running the lane with
# LUMOGIS_REQUIRE_INTEGRATION_DB=1 -- a plain run reported it as a silent skip.
psycopg2_extras = pytest.importorskip("psycopg2.extras")

_DDL = """
CREATE TABLE signals (
    signal_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      TEXT NOT NULL DEFAULT 'default',
    from_addr    TEXT NOT NULL DEFAULT '',
    from_display TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE email_sent_log (
    id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id   TEXT NOT NULL DEFAULT 'default',
    to_addrs  TEXT[] NOT NULL DEFAULT '{}',
    cc_addrs  TEXT[] NOT NULL DEFAULT '{}',
    bcc_addrs TEXT[] NOT NULL DEFAULT '{}',
    sent_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE email_sender_rule (
    user_id   TEXT NOT NULL,
    source_id UUID,
    from_addr TEXT NOT NULL,
    action    TEXT NOT NULL DEFAULT 'hide',
    active    BOOLEAN NOT NULL DEFAULT TRUE
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


@pytest.fixture
def db(monkeypatch):
    """Throwaway schema, with the real module pointed at a real driver."""
    schema = f"lum818_{uuid.uuid4().hex[:8]}"
    try:
        conn = psycopg2.connect(**_conn_kwargs())
    except Exception as exc:  # pragma: no cover - environment dependent
        require_postgres(exc)
        raise
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(f"SET search_path TO {schema}, public")
        cur.execute(_DDL)

    import config

    class _RealStore:
        def fetch_all(self, sql, params=None):
            with conn.cursor(cursor_factory=psycopg2_extras.RealDictCursor) as cur:
                cur.execute(f"SET search_path TO {schema}, public")
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]

        def fetch_one(self, sql, params=None):
            return None

        def execute(self, sql, params=None):
            with conn.cursor() as cur:
                cur.execute(f"SET search_path TO {schema}, public")
                cur.execute(sql, params)

    monkeypatch.setattr(config, "get_metadata_store", lambda: _RealStore())
    try:
        yield conn, schema
    finally:
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA {schema} CASCADE")
        conn.close()


def _seed(conn, schema: str) -> None:
    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {schema}, public")
        cur.execute(
            # NO params passed, so psycopg2 does not %-format: a single `%` here
            # stays a single `%` in the row. Doubling it would store a literal
            # `%%` and this file's own escaping assertions would drift.
            "INSERT INTO signals (user_id, from_addr, from_display) VALUES "
            "('alice','jane@example.com','Jane Smith'),"
            "('alice','100%pure@example.com','Hundred Percent'),"
            "('alice','a_b@example.com','Underscore Person'),"
            "('bob','bobs-friend@example.com','Bobs Friend')"
        )
        cur.execute(
            "INSERT INTO email_sent_log (user_id, to_addrs, cc_addrs, bcc_addrs) VALUES "
            "('alice','{colleague@example.com}','{cc-person@example.com}',"
            "'{lawyer@chambers.example}')"
        )


def _addrs(user, q):
    from services import email_recipients

    return [s.address for s in email_recipients.suggest_recipients(user, q)]


def test_a_blind_copied_address_never_comes_back_out(db):
    """The headline security property, end to end against a real database."""
    conn, schema = db
    _seed(conn, schema)

    assert _addrs("alice", "lawyer") == []
    assert _addrs("alice", "chambers") == []


def test_a_wildcard_query_matches_a_literal_percent_not_everything(db):
    """If `_escape_like` were wrong this would return the whole address book."""
    conn, schema = db
    _seed(conn, schema)

    assert _addrs("alice", "%%") == []
    assert _addrs("alice", "100%") == ["100%pure@example.com"]


def test_an_underscore_query_matches_a_literal_underscore(db):
    """`_` is a single-character wildcard in LIKE; escaped, it is a literal."""
    conn, schema = db
    _seed(conn, schema)

    assert _addrs("alice", "__") == []
    assert _addrs("alice", "a_b") == ["a_b@example.com"]


def test_the_sent_side_unnests_to_and_cc_in_postgres(db):
    """Array concatenation + unnest is engine behaviour, not Python list work."""
    conn, schema = db
    _seed(conn, schema)

    assert _addrs("alice", "colleague") == ["colleague@example.com"]
    assert _addrs("alice", "cc-person") == ["cc-person@example.com"]


def test_another_users_correspondents_are_invisible(db):
    conn, schema = db
    _seed(conn, schema)

    assert _addrs("alice", "bobs") == []
    assert _addrs("bob", "bobs") == ["bobs-friend@example.com"]


def test_a_display_name_match_works_against_the_real_column(db):
    conn, schema = db
    _seed(conn, schema)

    assert _addrs("alice", "Smith") == ["jane@example.com"]


def test_a_suppression_rule_removes_a_real_row(db):
    conn, schema = db
    _seed(conn, schema)
    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {schema}, public")
        cur.execute(
            "INSERT INTO email_sender_rule (user_id, from_addr, active) "
            "VALUES ('alice','jane@example.com',TRUE)"
        )

    assert _addrs("alice", "jane") == []
