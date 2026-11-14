# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-648 — the proactive-surfacing SQL executes against REAL Postgres.

The house fake store string-matches SQL and cannot execute it, so the three
correctness-critical statements are proven here against the migration-057 schema:

* the snooze-scan atomic ``UPDATE … RETURNING`` flips only due snoozes and returns
  the exact emit worklist (idempotent — a second run returns nothing);
* ``count_needs_you_email``'s ``COUNT(DISTINCT COALESCE(published_from, signal_id))``
  collapses a self-shared email to **1, not 2** (owner/projection dedup — Sonnet R2);
* the morning-brief enumeration ``signals ∪ signal_triage`` includes a triage-only
  household member who owns no signals (Fable R3 D1.1).

Skips when no Postgres is reachable so a laptop unit run stays green.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from auth import UserContext

# The flip statement is IMPORTED from production, never copied. It used to be a
# byte-identical hand-copy in this file — which is precisely why the defect it
# carried survived: the test reproduced the bug faithfully and failed honestly, in a
# lane that never ran because Postgres is not reachable in the usual dev/CI path. A
# copy can only ever prove that the copy is broken.
from signals.triage_reminders import FLIP_DUE_SNOOZES_SQL as _FLIP_SQL
from tests.integration._pgguard import require_postgres

from services import signal_triage

psycopg2 = pytest.importorskip("psycopg2")
pytest.importorskip("psycopg2.extras")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_MIGRATION_057 = _REPO_ROOT / "postgres" / "migrations" / "057-signal-triage.sql"

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

# Minimal users table for the flip's allows_shared LEFT JOIN (LUM-663).
_USERS_DDL = """
CREATE TABLE users (
    id            TEXT PRIMARY KEY,
    allows_shared BOOLEAN NOT NULL DEFAULT TRUE
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
    name = f"test_reminders_{uuid.uuid4().hex[:12]}"
    conn = psycopg2.connect(**_conn_kwargs())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{name}"')
            cur.execute(f'SET search_path TO "{name}"')
            cur.execute(_SIGNALS_DDL)
            cur.execute(_USERS_DDL)
            cur.execute(_MIGRATION_057.read_text(encoding="utf-8"))
        yield conn
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA "{name}" CASCADE')
        conn.close()


def _add_signal(
    conn,
    *,
    signal_id,
    user_id,
    scope,
    url,
    importance=0.9,
    published_from=None,
    title="",
    from_display="",
):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id, user_id, scope, url, importance_score, "
            "published_from, title, from_display) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (signal_id, user_id, scope, url, importance, published_from, title, from_display),
        )


def _add_user(conn, *, user_id, allows_shared=True):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, allows_shared) VALUES (%s, %s)", (user_id, allows_shared)
        )


def _run(conn, query, params):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]


def test_scan_flip_returns_worklist_with_copy_fields_and_is_idempotent(schema):
    conn = schema
    sid_due = str(uuid.uuid4())
    sid_future = str(uuid.uuid4())
    _add_signal(
        conn,
        signal_id=sid_due,
        user_id="A",
        scope="personal",
        url="imap://a/1/1",
        title="Rent due",
        from_display="Landlord",
    )
    _add_signal(conn, signal_id=sid_future, user_id="A", scope="personal", url="imap://a/2/2")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO signal_triage (user_id, signal_id, status, snoozed_until) "
            "VALUES ('A', %s, 'snoozed', now() - INTERVAL '1 hour')",
            (sid_due,),
        )
        cur.execute(
            "INSERT INTO signal_triage (user_id, signal_id, status, snoozed_until) "
            "VALUES ('A', %s, 'snoozed', now() + INTERVAL '1 hour')",
            (sid_future,),
        )

    worklist = _run(conn, _FLIP_SQL, ())
    assert [(r["user_id"], str(r["signal_id"])) for r in worklist] == [("A", sid_due)]
    # The copy fields come back from the flip in one round-trip (LUM-664).
    assert worklist[0]["title"] == "Rent due"
    assert worklist[0]["from_display"] == "Landlord"

    # The due row flipped; the future row is untouched.
    rows = _run(
        conn,
        "SELECT signal_id, status, snoozed_until FROM signal_triage ORDER BY status",
        (),
    )
    by_id = {str(r["signal_id"]): r for r in rows}
    assert by_id[sid_due]["status"] == "needs_you"
    assert by_id[sid_due]["snoozed_until"] is None
    assert by_id[sid_future]["status"] == "snoozed"

    # Idempotent: a second run has nothing due.
    assert _run(conn, _FLIP_SQL, ()) == []


def test_scan_does_not_flip_shared_when_allows_shared_false(schema):
    # LUM-663: a stale snooze on a shared signal a member can no longer see
    # (allows_shared=false) must NOT be flipped or surfaced.
    conn = schema
    shared = str(uuid.uuid4())
    _add_signal(
        conn,
        signal_id=shared,
        user_id="owner",
        scope="shared",
        url="imap://o/1/1",
        title="Shared thing",
    )
    _add_user(conn, user_id="B", allows_shared=False)  # B opted out of shared visibility
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO signal_triage (user_id, signal_id, status, snoozed_until) "
            "VALUES ('B', %s, 'snoozed', now() - INTERVAL '1 hour')",
            (shared,),
        )

    # The due snooze is on a now-invisible shared signal → not in the worklist.
    assert _run(conn, _FLIP_SQL, ()) == []
    still = _run(
        conn, "SELECT status FROM signal_triage WHERE user_id = 'B' AND signal_id = %s", (shared,)
    )
    assert still[0]["status"] == "snoozed"  # left untouched

    # Flip the same user's flag to TRUE → now the shared signal is visible → it flips.
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET allows_shared = TRUE WHERE id = 'B'")
    worklist = _run(conn, _FLIP_SQL, ())
    assert [(r["user_id"], str(r["signal_id"])) for r in worklist] == [("B", shared)]


def _count_query(conn, user_id: str) -> int:
    """Assemble + run the exact count_needs_you_email SQL for a household-union user.

    Uses an explicit allows_shared=True context (the schema has no ``users`` table,
    which ``get_allows_shared`` would read) — orthogonal to the dedup under test.
    """
    from visibility import visible_filter

    user = UserContext(user_id=user_id, role="user", is_authenticated=True, allows_shared=True)
    where_clause, where_params = visible_filter(user, None)
    where = (
        where_clause + " AND url LIKE %s AND ("
        "t.triage_status = 'needs_you' "
        "OR (t.triage_status IS NULL AND (notified OR importance_score >= %s)) "
        "OR (t.triage_status = 'snoozed' AND t.snoozed_until <= now()))"
    )
    sql = (
        "SELECT COUNT(DISTINCT COALESCE(published_from, signal_id)) AS n "
        f"FROM signals {signal_triage._TRIAGE_LATERAL} WHERE {where}"
    )
    params = (user_id, *where_params, "imap://%", 0.6)
    return _run(conn, sql, params)[0]["n"]


def test_count_dedups_self_shared_email_to_one(schema):
    conn = schema
    personal = str(uuid.uuid4())
    shared = str(uuid.uuid4())
    # A self-shares her own needs-you email: personal original + shared projection
    # (published_from = the personal id), both importance>=0.6, both email.
    _add_signal(conn, signal_id=personal, user_id="A", scope="personal", url="imap://a/1/1")
    _add_signal(
        conn,
        signal_id=shared,
        user_id="A",
        scope="shared",
        url="imap://a/1/1",
        published_from=personal,
    )
    assert _count_query(conn, "A") == 1  # not 2


def test_brief_enumeration_includes_triage_only_member(schema):
    conn = schema
    # A owns a shared signal; B owns nothing but triages it needs_you.
    sid = str(uuid.uuid4())
    _add_signal(conn, signal_id=sid, user_id="A", scope="shared", url="imap://a/1/1")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO signal_triage (user_id, signal_id, status) VALUES ('B', %s, 'needs_you')",
            (sid,),
        )

    rows = _run(
        conn,
        "SELECT DISTINCT user_id FROM signals WHERE user_id IS NOT NULL "
        "UNION SELECT DISTINCT user_id FROM signal_triage WHERE user_id IS NOT NULL "
        "ORDER BY user_id",
        (),
    )
    assert {r["user_id"] for r in rows} == {"A", "B"}


# ---------------------------------------------------------------------------
# LUM-717 — the SIBLING query executes too
# ---------------------------------------------------------------------------
#
# The flip was not the only silently-caught SQL in this feature. The morning
# brief's count runs through `triage_surfacing.count_needs_you_email`, whose
# `except Exception: return 0` has the same shape as `_scan_due_snoozes`'s — and
# the brief "stays silent when the count is zero". So a broken count is not a
# noisy failure: it is a brief that never fires, for anyone, forever. Exactly the
# way the flip defect hid. It is asserted here rather than assumed working.


def test_the_morning_brief_count_query_executes_against_real_postgres(schema, monkeypatch):
    conn = schema
    import config
    from services import triage_surfacing

    mine, other, done = (str(uuid.uuid4()) for _ in range(3))
    _add_signal(conn, signal_id=mine, user_id="A", scope="personal", url="imap://a/1/1")
    _add_signal(conn, signal_id=other, user_id="B", scope="personal", url="imap://b/1/1")
    _add_signal(conn, signal_id=done, user_id="A", scope="personal", url="imap://a/1/2")
    _add_user(conn, user_id="A", allows_shared=True)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO signal_triage (user_id, signal_id, status) "
            "VALUES (%s, %s, %s), (%s, %s, %s)",
            ("A", mine, "needs_you", "A", done, "done"),
        )

    captured = {}

    class _Capture:
        def fetch_one(self, sql, params=None):
            captured["sql"], captured["params"] = sql, params
            return {"n": 0}

        def fetch_all(self, sql, params=None):
            return []

    monkeypatch.setattr(config, "get_metadata_store", lambda: _Capture())
    triage_surfacing.count_needs_you_email("A")

    # Execute the EXACT statement the brief runs. A 42702 or a bad bind here would
    # be swallowed by the caller's `except` and reported as "0 needing you".
    rows = _run(conn, captured["sql"], captured["params"])

    assert rows and rows[0]["n"] == 1, (
        "only A's explicitly-needs_you email counts — not B's, not A's done one"
    )
