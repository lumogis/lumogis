# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Regression tests for migration 022-action-proposals-claim."""

from __future__ import annotations

import contextlib
import os
import uuid
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MIGRATIONS = _REPO_ROOT / "postgres" / "migrations"

# 022 is the migration under test. 062 is here because the SQL under test is
# IMPORTED from production (`proposal_queue.CLAIM_BY_ID_SQL` / `DEAD_LETTER_SQL`,
# after LUM-762 deleted `CLAIM_NEXT_SQL` with the drain) rather than copied,
# so it moves when production moves: 062 added `action_proposals.annotation` and
# the claim statement now returns it. Against a schema built from 022 alone the
# statement fails with UndefinedColumn — which it had been doing unnoticed, since
# this test skips without a real Postgres (LUM-718).
#
# 047 and 066 also mention action_proposals but are deliberately absent: 047 only
# adds a foreign key *from* web_messages (a table this bare schema does not have,
# so it cannot apply), and 066 mentions it only in a comment. Neither adds a
# column the claim statement reads.
#
# This list is not self-maintaining, and that is acceptable: a future migration
# adding another claimed column makes this test FAIL loudly rather than pass
# vacuously, because the statement comes from production. Add the file here when
# it does.
_MIGRATION_PATHS = [
    _MIGRATIONS / "022-action-proposals-claim.sql",
    _MIGRATIONS / "062-email-smtp-send.sql",
]


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
def _pg_available() -> None:
    try:
        conn = psycopg2.connect(**_conn_kwargs())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable: {exc}")
    conn.close()


@pytest.fixture
def schema(_pg_available):
    name = f"test_mig022_{uuid.uuid4().hex[:12]}"
    conn = psycopg2.connect(**_conn_kwargs())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{name}"')
            cur.execute(f'SET search_path TO "{name}"')
        yield conn, name
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        conn.close()


def _apply_migration(conn, schema_name: str) -> None:
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema_name}"')
        for path in _MIGRATION_PATHS:
            cur.execute(path.read_text(encoding="utf-8"))


def test_migration_022_creates_table_and_indexes(schema):
    conn, schema_name = schema
    _apply_migration(conn, schema_name)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = %s AND table_name = 'action_proposals'
            ORDER BY ordinal_position
            """,
            (schema_name,),
        )
        cols = {r[0] for r in cur.fetchall()}
    assert "user_id" in cols
    assert "action_name" in cols
    assert "payload" in cols
    assert "claimed_at" in cols
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT indexname FROM pg_indexes
            WHERE schemaname = %s AND tablename = 'action_proposals'
            """,
            (schema_name,),
        )
        idx = {r[0] for r in cur.fetchall()}
    assert "action_proposals_approved_claim_id_idx" in idx
    assert "action_proposals_active_claim_ts_idx" in idx


def test_migration_022_is_idempotent(schema):
    conn, schema_name = schema
    _apply_migration(conn, schema_name)
    _apply_migration(conn, schema_name)


def test_migration_022_invalid_status_insert_fails(schema):
    conn, schema_name = schema
    _apply_migration(conn, schema_name)
    from psycopg2 import errors as pg_errors

    with pytest.raises(pg_errors.CheckViolation):
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema_name}"')
            cur.execute(
                "INSERT INTO action_proposals (user_id, action_name, payload, status) "
                "VALUES (%s, %s, %s::jsonb, %s)",
                ("u", "a", "{}", "bad_status"),
            )


@contextlib.contextmanager
def _seeded_schema(rows):
    """Isolated schema + migrations + seeded `action_proposals` rows.

    ``rows`` is a list of ``(user_id, action_name, status, offset_minutes)`` where
    ``offset_minutes`` shifts ``run_after`` relative to ``NOW()`` (negative = ready,
    positive = deferred, ``0``/``None`` = now). Yields ``(setup_conn, schema_name, ids)``.

    The offset is **parameterised**, not interpolated: the plan's own Definition of
    done forbids raw SQL string interpolation in any modified file, and a test helper
    is not exempt from a rule about SQL construction just because its inputs happen to
    be literals today.
    """
    schema_name = f"test_mig022_{uuid.uuid4().hex[:12]}"
    setup = psycopg2.connect(**_conn_kwargs())
    setup.autocommit = True
    ids = []
    try:
        with setup.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema_name}"')
            cur.execute(f'SET search_path TO "{schema_name}"')
            for path in _MIGRATION_PATHS:
                cur.execute(path.read_text(encoding="utf-8"))
            for user_id, action_name, status, offset_minutes in rows:
                cur.execute(
                    "INSERT INTO action_proposals "
                    "(user_id, action_name, payload, status, run_after) "
                    "VALUES (%s, %s, %s::jsonb, %s, NOW() + (%s * INTERVAL '1 minute')) "
                    "RETURNING id",
                    (user_id, action_name, '{"k": true}', status, int(offset_minutes or 0)),
                )
                ids.append(int(cur.fetchone()[0]))
        yield setup, schema_name, ids
    finally:
        with setup.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        setup.close()


def _claim_conn(schema_name):
    """A connection shaped like production: autocommit ON.

    `PostgresStore` sets `conn.autocommit = True` (adapters/postgres_store.py:21), so
    every statement commits on execution and row locks last only for the statement.
    Tests that model the claim race MUST match that, otherwise they are asserting
    against a locking regime the product never runs in.
    """
    conn = psycopg2.connect(options=f'-c search_path="{schema_name}"', **_conn_kwargs())
    conn.autocommit = True
    return conn


def test_claim_by_id_two_workers_do_not_claim_same_row(_pg_available):
    """Two household clients press Execute on one approved proposal; one wins.

    LUM-762 re-pointed this from `CLAIM_NEXT_SQL` to `CLAIM_BY_ID_SQL` — the path that
    actually runs. **The re-point is not verbatim, and must not be.** `_CLAIM_NEXT_SQL`
    carried `FOR UPDATE SKIP LOCKED`, which is the only reason the old structure worked:
    two non-autocommit connections, worker B executing while worker A's transaction was
    still open, and `worker_a.commit()` on a LATER line. `_CLAIM_BY_ID_SQL` is a bare
    `UPDATE … WHERE` with no SKIP LOCKED — worker B would not skip, it would BLOCK on
    worker A's uncommitted row lock, and worker A never commits because the commit is
    below. That deadlocks the suite until a statement timeout that is not configured.

    With autocommit (what production does) worker A's UPDATE commits on execution and
    worker B's re-evaluates the predicate under READ COMMITTED, finds `claimed_at IS NOT
    NULL`, and matches zero rows. That is the guarantee `claim_and_execute_proposal`
    actually relies on to return its 409.
    """
    from services import proposal_queue

    seeded = [("alice", "lumogis.tests.noop", "approved", None)]  # run_after = NOW()
    with _seeded_schema(seeded) as (_setup, schema_name, ids):
        pid = ids[0]
        worker_a = _claim_conn(schema_name)
        worker_b = _claim_conn(schema_name)
        try:
            with worker_a.cursor() as cur_a:
                cur_a.execute(proposal_queue.CLAIM_BY_ID_SQL, ("worker-a", pid, "alice"))
                row_a = cur_a.fetchone()
            with worker_b.cursor() as cur_b:
                cur_b.execute(proposal_queue.CLAIM_BY_ID_SQL, ("worker-b", pid, "alice"))
                row_b = cur_b.fetchone()
            assert row_a is not None, "first worker must claim"
            assert row_b is None, "second worker must lose the claim, not block on it"
        finally:
            worker_a.close()
            worker_b.close()


def test_claim_by_id_sql_refuses_future_run_after(_pg_available):
    """Mutation B — remove `AND run_after <= NOW()` from production and this must fail.

    Cannot be proven at the unit layer: `FakeProposalQueueStore` pattern-matches the
    query string and re-implements the predicate in Python, so a fake-lane assertion
    proves the fake was edited, not the statement (LUM-771).
    """
    from services import proposal_queue

    rows = [("alice", "lumogis.tests.noop", "approved", 5)]  # deferred 5 min
    with _seeded_schema(rows) as (_setup, schema_name, ids):
        pid = ids[0]
        conn = _claim_conn(schema_name)
        try:
            with conn.cursor() as cur:
                cur.execute(proposal_queue.CLAIM_BY_ID_SQL, ("worker-a", pid, "alice"))
                assert cur.fetchone() is None, "a deferred row must not be claimable"
                cur.execute("SELECT status FROM action_proposals WHERE id = %s", (pid,))
                assert cur.fetchone()[0] == "approved", "and must not be consumed"
        finally:
            conn.close()


def test_claim_by_id_sql_denied_cross_user(_pg_available):
    """Mutation C — drop `AND user_id = %s` from production and this must fail.

    This test exists because the fake-lane equivalent CANNOT detect that mutation.
    `fake_proposal_queue_store.py` gates its claim branch on the literal substring
    `"where id = %s and user_id = %s"`; remove the predicate and the string simply
    stops matching, `fetch_one` falls through to `return None`, and a test asserting
    `is None` goes green. Cross-user isolation is the one security property this
    module carries, so it is proven here or not at all.
    """
    from services import proposal_queue

    seeded = [("alice", "lumogis.tests.noop", "approved", None)]  # run_after = NOW()
    with _seeded_schema(seeded) as (_setup, schema_name, ids):
        pid = ids[0]
        conn = _claim_conn(schema_name)
        try:
            with conn.cursor() as cur:
                cur.execute(proposal_queue.CLAIM_BY_ID_SQL, ("worker-bob", pid, "bob"))
                assert cur.fetchone() is None, "bob must not claim alice's proposal"
                cur.execute("SELECT status, claimed_by FROM action_proposals WHERE id = %s", (pid,))
                status, claimed_by = cur.fetchone()
                assert status == "approved" and claimed_by is None
        finally:
            conn.close()


def test_dead_letter_sql_leaves_run_after_untouched(_pg_available):
    """Mutation F — restore `run_after = NOW()` in `_DEAD_LETTER_SQL` and this must fail.

    `run_after` belongs to LUM-147's `NOT_NOW` deferral; a dead row's deferral time is
    not this code's to decide. Undetectable at the fake layer however that branch is
    written, because the fake re-implements the disposal in Python.
    """
    from services import proposal_queue

    # 42 = a deferral dead_letter must not clobber
    rows = [("alice", "lumogis.tests.noop", "executing", 42)]
    with _seeded_schema(rows) as (_setup, schema_name, ids):
        pid = ids[0]
        conn = _claim_conn(schema_name)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT run_after FROM action_proposals WHERE id = %s", (pid,))
                before = cur.fetchone()[0]
                cur.execute(proposal_queue.DEAD_LETTER_SQL, ("boom", pid))
                cur.execute(
                    "SELECT status, run_after, attempt FROM action_proposals WHERE id = %s",
                    (pid,),
                )
                status, after, attempt = cur.fetchone()
            assert status == "dead"
            assert after == before, "dead_letter must not stamp run_after"
            assert attempt == 1
        finally:
            conn.close()
