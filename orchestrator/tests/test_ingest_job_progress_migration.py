# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Regression tests for migration 035-ingest-job-progress (LUM-511)."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MIGRATION_017 = _REPO_ROOT / "postgres" / "migrations" / "017-per-user-batch-jobs.sql"
_MIGRATION_035 = _REPO_ROOT / "postgres" / "migrations" / "035-ingest-job-progress.sql"


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
    name = f"test_mig035_{uuid.uuid4().hex[:12]}"
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


def _apply_migrations(conn, schema_name: str) -> None:
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema_name}"')
        cur.execute(_MIGRATION_017.read_text(encoding="utf-8"))
        cur.execute(_MIGRATION_035.read_text(encoding="utf-8"))


def test_migration_035_adds_progress_columns_and_batch_index(schema):
    conn, schema_name = schema
    _apply_migrations(conn, schema_name)
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
    assert {"progress_stage", "progress_pct", "progress_message"}.issubset(cols)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT indexname FROM pg_indexes
            WHERE schemaname = %s AND tablename = 'user_batch_jobs'
            """,
            (schema_name,),
        )
        idx = {r[0] for r in cur.fetchall()}
    assert "user_batch_jobs_batch_id_idx" in idx


def test_migration_035_progress_pct_check_constraint(schema):
    conn, schema_name = schema
    _apply_migrations(conn, schema_name)
    from psycopg2 import errors as pg_errors

    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema_name}"')
        cur.execute(
            """
            INSERT INTO user_batch_jobs (user_id, kind, payload, progress_pct)
            VALUES (%s, %s, %s::jsonb, %s)
            """,
            ("alice", "ingest_upload", '{"file_id": "f1"}', 50),
        )
    with pytest.raises(pg_errors.CheckViolation):
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema_name}"')
            cur.execute(
                """
                INSERT INTO user_batch_jobs (user_id, kind, payload, progress_pct)
                VALUES (%s, %s, %s::jsonb, %s)
                """,
                ("alice", "ingest_upload", '{"file_id": "f2"}', 101),
            )


def test_migration_035_is_idempotent(schema):
    conn, schema_name = schema
    _apply_migrations(conn, schema_name)
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema_name}"')
        cur.execute(_MIGRATION_035.read_text(encoding="utf-8"))
