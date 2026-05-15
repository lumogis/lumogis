# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Regression tests for migration 022-action-proposals-claim."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MIGRATION_PATH = _REPO_ROOT / "postgres" / "migrations" / "022-action-proposals-claim.sql"


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
    sql = _MIGRATION_PATH.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema_name}"')
        cur.execute(sql)


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


def test_claim_next_two_workers_do_not_claim_same_row(_pg_available):
    from services import proposal_queue

    schema_name = f"test_mig022_{uuid.uuid4().hex[:12]}"
    setup = psycopg2.connect(**_conn_kwargs())
    setup.autocommit = True
    try:
        with setup.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema_name}"')
            cur.execute(f'SET search_path TO "{schema_name}"')
            cur.execute(_MIGRATION_PATH.read_text(encoding="utf-8"))
            cur.execute(
                "INSERT INTO action_proposals (user_id, action_name, payload, status) "
                "VALUES (%s, %s, %s::jsonb, %s)",
                ("alice", "lumogis.tests.noop", '{"k": true}', "approved"),
            )
        opts = f'-c search_path="{schema_name}"'
        worker_a = psycopg2.connect(options=opts, **_conn_kwargs())
        worker_b = psycopg2.connect(options=opts, **_conn_kwargs())
        try:
            with worker_a.cursor() as cur_a:
                cur_a.execute(proposal_queue.CLAIM_NEXT_SQL, ("worker-a",))
                row_a = cur_a.fetchone()
            with worker_b.cursor() as cur_b:
                cur_b.execute(proposal_queue.CLAIM_NEXT_SQL, ("worker-b",))
                row_b = cur_b.fetchone()
            assert row_a is not None, "first worker must claim"
            assert row_b is None, "second SKIP LOCKED"
            worker_a.commit()
            worker_b.commit()
        finally:
            worker_a.close()
            worker_b.close()
    finally:
        with setup.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        setup.close()
