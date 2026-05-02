# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Regression tests for migration 017-per-user-batch-jobs."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MIGRATION_PATH = _REPO_ROOT / "postgres" / "migrations" / "017-per-user-batch-jobs.sql"


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
    name = f"test_mig017_{uuid.uuid4().hex[:12]}"
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
    sql = _MIGRATION_PATH.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema_name}"')
        cur.execute(sql)


def test_migration_017_creates_table_and_indexes(schema):
    conn, schema_name = schema
    _apply_migration(conn, schema_name)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = %s AND table_name = 'user_batch_jobs'
            ORDER BY ordinal_position
            """,
            (schema_name,),
        )
        cols = {r[0] for r in cur.fetchall()}
    assert "user_id" in cols
    assert "kind" in cols
    assert "payload" in cols
    assert "status" in cols
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT indexname FROM pg_indexes
            WHERE schemaname = %s AND tablename = 'user_batch_jobs'
            """,
            (schema_name,),
        )
        idx = {r[0] for r in cur.fetchall()}
    # VERIFY-PLAN: assert all four indexes named in plan §Test cases 29
    assert "user_batch_jobs_pending_claim_idx" in idx
    assert "user_batch_jobs_running_per_user_idx" in idx
    assert "user_batch_jobs_running_started_idx" in idx
    assert "user_batch_jobs_user_status_idx" in idx


def test_migration_017_is_idempotent(schema):
    conn, schema_name = schema
    _apply_migration(conn, schema_name)
    _apply_migration(conn, schema_name)


# VERIFY-PLAN (closure): plan §Test cases 6 — two workers must not claim the
# same row under FOR UPDATE SKIP LOCKED. Uses the canonical _CLAIM_SQL from
# services.batch_queue verbatim so any future SQL drift surfaces here.
def test_claim_next_two_workers_do_not_claim_same_row(_pg_available):
    from services import batch_queue

    schema_name = f"test_mig017_{uuid.uuid4().hex[:12]}"
    setup = psycopg2.connect(**_conn_kwargs())
    setup.autocommit = True
    try:
        with setup.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema_name}"')
            cur.execute(f'SET search_path TO "{schema_name}"')
            cur.execute(_MIGRATION_PATH.read_text(encoding="utf-8"))
            cur.execute(
                "INSERT INTO user_batch_jobs (user_id, kind, payload) "
                "VALUES (%s, %s, %s::jsonb)",
                ("alice", "ingest_folder", '{"path": "/tmp"}'),
            )
        # Two competing workers, each in its own connection with
        # autocommit=False so the row-lock from the first claim
        # survives long enough for the second to observe SKIP LOCKED.
        opts = f'-c search_path="{schema_name}"'
        worker_a = psycopg2.connect(options=opts, **_conn_kwargs())
        worker_b = psycopg2.connect(options=opts, **_conn_kwargs())
        try:
            cap = batch_queue.BATCH_QUEUE_PER_USER_MAX_CONCURRENT
            with worker_a.cursor() as cur_a:
                cur_a.execute(batch_queue._CLAIM_SQL, (cap, "worker-a"))
                row_a = cur_a.fetchone()
            with worker_b.cursor() as cur_b:
                cur_b.execute(batch_queue._CLAIM_SQL, (cap, "worker-b"))
                row_b = cur_b.fetchone()
            assert row_a is not None, "first worker must claim the only pending row"
            assert row_b is None, (
                "second worker must SKIP LOCKED the row already claimed by worker-a"
            )
            worker_a.commit()
            worker_b.commit()
        finally:
            worker_a.close()
            worker_b.close()
    finally:
        with setup.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        setup.close()
