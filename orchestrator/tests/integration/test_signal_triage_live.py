# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-643 — the triage LATERAL executes against REAL Postgres (42702-free).

The house fake store string-matches SQL and cannot execute it, so a binding or
ambiguity bug (e.g. a plain ``LEFT JOIN signal_triage`` — both tables carry
``user_id``/``signal_id`` and the SELECT + ``visible_filter`` use *unqualified*
columns → ``42702 ambiguous_column``) would ship green there and be silently
blanked by ``list_signals``' catch-all. This companion executes the EXACT query
``routes.signals.list_signals`` runs (``signal_triage.build_signals_query``)
against the migration-057 schema, for BOTH the base recency query and the
``triaged=set`` query — proving they parse, bind, and scope correctly.

Skips when no Postgres is reachable so a laptop unit run stays green; in CI /
``make compose-test`` Postgres is up and the test runs. An isolated schema per
test keeps parallel runs from colliding.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from auth import UserContext
from tests.integration._pgguard import require_postgres

import config
from services import signal_triage

psycopg2 = pytest.importorskip("psycopg2")
pytest.importorskip("psycopg2.extras")  # registers psycopg2.extras (RealDictCursor)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_MIGRATION_057 = _REPO_ROOT / "postgres" / "migrations" / "057-signal-triage.sql"

# Minimal signals table carrying exactly the columns build_signals_query selects
# (the migration's FK targets signals(signal_id)).
_SIGNALS_DDL = """
CREATE TABLE signals (
    signal_id        UUID PRIMARY KEY,
    user_id          TEXT NOT NULL DEFAULT 'default',
    source_id        TEXT NOT NULL DEFAULT '',
    title            TEXT NOT NULL DEFAULT '',
    url              TEXT NOT NULL DEFAULT '',
    published_at     TIMESTAMPTZ,
    content_summary  TEXT NOT NULL DEFAULT '',
    entities         JSONB NOT NULL DEFAULT '[]',
    topics           JSONB NOT NULL DEFAULT '[]',
    importance_score FLOAT NOT NULL DEFAULT 0.0,
    relevance_score  FLOAT NOT NULL DEFAULT 0.0,
    notified         BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    category         TEXT NOT NULL DEFAULT '',
    from_addr        TEXT NOT NULL DEFAULT '',
    from_display     TEXT NOT NULL DEFAULT '',
    scope            TEXT NOT NULL DEFAULT 'personal',
    published_from   UUID,
    source_url       TEXT,
    source_label     TEXT
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


@pytest.fixture(scope="module")
def _pg_available() -> bool:
    try:
        conn = psycopg2.connect(**_conn_kwargs())
    except Exception as exc:  # noqa: BLE001 — diagnostic skip
        require_postgres(exc)
    conn.close()
    return True


@pytest.fixture
def schema(_pg_available):
    name = f"test_triage_{uuid.uuid4().hex[:12]}"
    conn = psycopg2.connect(**_conn_kwargs())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{name}"')
            cur.execute(f'SET search_path TO "{name}"')
            cur.execute(_SIGNALS_DDL)
            cur.execute(_MIGRATION_057.read_text(encoding="utf-8"))
        yield conn
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA "{name}" CASCADE')
        conn.close()


def _add_signal(conn, *, signal_id, user_id, scope, url):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id, user_id, scope, url) VALUES (%s, %s, %s, %s)",
            (signal_id, user_id, scope, url),
        )


def _add_triage(conn, *, user_id, signal_id, status):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO signal_triage (user_id, signal_id, status) VALUES (%s, %s, %s)",
            (user_id, signal_id, status),
        )


def _run(conn, query, params):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]


def test_base_lateral_query_executes_and_returns_caller_triage(schema):
    conn = schema
    from visibility import visible_filter

    sid = str(uuid.uuid4())
    _add_signal(conn, signal_id=sid, user_id="A", scope="personal", url="imap://a/1/1")
    _add_triage(conn, user_id="A", signal_id=sid, status="done")

    where_clause, where_params = visible_filter(
        UserContext(user_id="A", is_authenticated=True, role="user", allows_shared=True)
    )
    query = signal_triage.build_signals_query(where_clause, "created_at DESC")
    # Caller-id (LATERAL) FIRST, then visible_filter params, then LIMIT.
    params = ("A", *where_params, 50)

    rows = _run(conn, query, params)  # 42702 would raise here
    assert len(rows) == 1
    assert rows[0]["triage_status"] == "done"
    assert rows[0]["published_from"] is None


def test_base_lateral_query_hides_other_members_triage(schema):
    conn = schema
    from visibility import visible_filter

    sid = str(uuid.uuid4())
    _add_signal(conn, signal_id=sid, user_id="B", scope="shared", url="imap://b/1/1")
    _add_triage(conn, user_id="A", signal_id=sid, status="done")  # Alice's triage

    # Bob reads the same shared signal — must see NULL triage (not Alice's).
    where_clause, where_params = visible_filter(
        UserContext(user_id="B", is_authenticated=True, role="user", allows_shared=True)
    )
    query = signal_triage.build_signals_query(where_clause, "created_at DESC")
    rows = _run(conn, query, ("B", *where_params, 50))
    assert len(rows) == 1
    assert rows[0]["triage_status"] is None


def test_triaged_set_query_executes_and_keeps_source_type_filter(schema):
    conn = schema
    from visibility import visible_filter

    mail, rss = str(uuid.uuid4()), str(uuid.uuid4())
    _add_signal(conn, signal_id=mail, user_id="A", scope="personal", url="imap://a/1/1")
    _add_signal(conn, signal_id=rss, user_id="A", scope="personal", url="https://news/x")
    _add_triage(conn, user_id="A", signal_id=mail, status="done")
    _add_triage(conn, user_id="A", signal_id=rss, status="done")

    where_clause, where_params = visible_filter(
        UserContext(user_id="A", is_authenticated=True, role="user", allows_shared=True)
    )
    # Mirror the route: base union + url LIKE + triaged filter; triage-recency order.
    where = where_clause + " AND url LIKE %s AND t.triage_status IS NOT NULL"
    query = signal_triage.build_signals_query(where, "t.triage_updated_at DESC")
    params = ("A", *where_params, "imap://%", 200)

    rows = _run(conn, query, params)  # 42702-free on the triaged arm too
    ids = {str(r["signal_id"]) for r in rows}
    assert ids == {mail}  # only the email triaged row; RSS excluded by source_type


def test_triaged_before_cursor_executes_and_pages(schema):
    # LUM-659: the keyset-pagination SQL (adds `AND t.triage_updated_at < %s::
    # timestamptz`) parses/binds against real Postgres and returns only older rows.
    conn = schema
    from visibility import visible_filter

    older, newer = str(uuid.uuid4()), str(uuid.uuid4())
    _add_signal(conn, signal_id=older, user_id="A", scope="personal", url="imap://a/1/1")
    _add_signal(conn, signal_id=newer, user_id="A", scope="personal", url="imap://a/2/2")
    _add_triage(conn, user_id="A", signal_id=older, status="done")
    # Force a strictly-later triage timestamp on `newer` so the cursor is unambiguous.
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO signal_triage (user_id, signal_id, status, updated_at) "
            "VALUES (%s, %s, 'done', NOW() + INTERVAL '1 hour')",
            ("A", newer),
        )

    where_clause, where_params = visible_filter(
        UserContext(user_id="A", is_authenticated=True, role="user", allows_shared=True)
    )
    where = (
        where_clause + " AND t.triage_status IS NOT NULL AND t.triage_updated_at < %s::timestamptz"
    )
    query = signal_triage.build_signals_query(where, "t.triage_updated_at DESC")
    # Cursor = the newer row's triage time → only the older row is returned.
    rows_all = _run(
        conn,
        signal_triage.build_signals_query(
            where_clause + " AND t.triage_status IS NOT NULL", "t.triage_updated_at DESC"
        ),
        ("A", *where_params, 50),
    )
    cursor = next(r for r in rows_all if str(r["signal_id"]) == newer)["triage_updated_at"]
    rows = _run(conn, query, ("A", *where_params, cursor, 50))
    assert {str(r["signal_id"]) for r in rows} == {older}


# ---------------------------------------------------------------------------
# LUM-696 — the two BULK statements execute against real Postgres
# ---------------------------------------------------------------------------
#
# Same reason the file exists: the house fake string-matches SQL and cannot
# execute it, so an ambiguous-column bug ships green everywhere else. The bulk
# statements are more exposed than the LATERAL, not less — the first draft of the
# LUM-696 plan wrote the clear as `DELETE … USING signals`, which puts BOTH
# signal_triage and signals in one scope while `visible_filter` emits UNQUALIFIED
# `user_id`, i.e. a guaranteed 42702 at runtime. The CTE form is what these two
# tests prove actually parses, binds, and scopes.


def _ctx(user_id: str) -> UserContext:
    return UserContext(user_id=user_id, is_authenticated=True, role="user", allows_shared=True)


def _bulk_params_set(ids, where_params, user_id, status, snoozed_until=None):
    # CTE binds first: (ids, *visible_params, user_id, status, snoozed_until)
    return (ids, *where_params, user_id, status, snoozed_until)


def test_bulk_set_executes_against_real_postgres(schema, monkeypatch):
    """42702-free, and `ON CONFLICT DO UPDATE … RETURNING` really does surface
    conflict-updated rows — proven by Postgres, not by a paragraph."""
    conn = schema
    from visibility import visible_filter

    fresh, existing = str(uuid.uuid4()), str(uuid.uuid4())
    _add_signal(conn, signal_id=fresh, user_id="A", scope="personal", url="imap://a/1/1")
    _add_signal(conn, signal_id=existing, user_id="A", scope="personal", url="imap://a/1/2")
    _add_triage(conn, user_id="A", signal_id=existing, status="needs_you")

    where_clause, where_params = visible_filter(_ctx("A"))

    captured = {}

    class _Capture:
        def fetch_all(self, sql, params=None):
            captured["sql"], captured["params"] = sql, params
            return []

    monkeypatch.setattr(config, "get_metadata_store", lambda: _Capture())
    signal_triage.set_triage_bulk(
        "A",
        [fresh, existing],
        "done",
        None,
        visible_clause=where_clause,
        visible_params=where_params,
    )

    rows = _run(conn, captured["sql"], captured["params"])  # 42702 would raise here

    assert {str(r["signal_id"]) for r in rows} == {fresh, existing}
    # The already-triaged row came back WITH its new status: DO UPDATE, not
    # DO NOTHING. A one-word slip would leave this row's status NULL.
    by_id = {str(r["signal_id"]): r for r in rows}
    assert by_id[existing]["status"] == "done"
    assert by_id[fresh]["status"] == "done"

    stored = _run(conn, "SELECT signal_id, status FROM signal_triage ORDER BY signal_id", ())
    assert {r["status"] for r in stored} == {"done"}


def test_bulk_clear_executes_against_real_postgres(schema, monkeypatch):
    """The statement that actually carried the ambiguous-column bug.

    Three things at once: rows with a triage row are deleted; a **visible but
    never-triaged** id still comes back (the data-modifying `deleted` CTE runs,
    and the final SELECT reads `visible`); another member's personal signal is
    neither deleted nor returned.
    """
    conn = schema
    from visibility import visible_filter

    triaged, untriaged, foreign = (str(uuid.uuid4()) for _ in range(3))
    _add_signal(conn, signal_id=triaged, user_id="A", scope="personal", url="imap://a/1/1")
    _add_signal(conn, signal_id=untriaged, user_id="A", scope="personal", url="imap://a/1/2")
    _add_signal(conn, signal_id=foreign, user_id="B", scope="personal", url="imap://b/1/1")
    _add_triage(conn, user_id="A", signal_id=triaged, status="done")
    _add_triage(conn, user_id="A", signal_id=foreign, status="done")

    where_clause, where_params = visible_filter(_ctx("A"))

    captured = {}

    class _Capture:
        def fetch_all(self, sql, params=None):
            captured["sql"], captured["params"] = sql, params
            return []

    monkeypatch.setattr(config, "get_metadata_store", lambda: _Capture())
    signal_triage.clear_triage_bulk(
        "A",
        [triaged, untriaged, foreign],
        visible_clause=where_clause,
        visible_params=where_params,
    )

    rows = _run(conn, captured["sql"], captured["params"])  # 42702 would raise here
    returned = {str(r["signal_id"]) for r in rows}

    # The round-1 P0, proven against the database: the visible-but-never-triaged
    # id is in the result, so the route reports it as success rather than "no
    # longer available".
    assert returned == {triaged, untriaged}
    assert foreign not in returned, "another member's personal signal is invisible"

    left = _run(conn, "SELECT signal_id FROM signal_triage", ())
    assert {str(r["signal_id"]) for r in left} == {foreign}, (
        "A's row deleted; B's untouched — the DELETE ran despite the final SELECT reading `visible`"
    )
