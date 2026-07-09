# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Regression tests for migration 043-biography-conflict-resolutions (LUM-514)."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MIGRATION_PATH = _REPO_ROOT / "postgres" / "migrations" / "043-biography-conflict-resolutions.sql"


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
    name = f"test_mig043_{uuid.uuid4().hex[:12]}"
    conn = psycopg2.connect(**_conn_kwargs())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{name}"')
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


def test_migration_043_creates_table_and_indexes(schema) -> None:
    conn, schema_name = schema
    _apply_migration(conn, schema_name)
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema_name}"')
        cur.execute(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname = %s AND tablename = 'biography_conflict_resolutions'
            """,
            (schema_name,),
        )
        assert cur.fetchone() is not None
        cur.execute(
            """
            SELECT indexname FROM pg_indexes
            WHERE schemaname = %s AND tablename = 'biography_conflict_resolutions'
            """,
            (schema_name,),
        )
        names = {r[0] for r in cur.fetchall()}
    assert "uq_biography_conflicts_open_fact_group" in names
    assert "idx_biography_conflicts_status" in names
    assert "idx_biography_conflicts_fact_group" in names


def test_migration_043_open_fact_group_unique(schema) -> None:
    conn, schema_name = schema
    _apply_migration(conn, schema_name)
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema_name}"')
        cur.execute(
            """
            INSERT INTO biography_conflict_resolutions (
                fact_group_key, category, pin_ids, detection_snapshot
            ) VALUES (
                'logistics|household|dinner',
                'logistics',
                ARRAY['00000000-0000-0000-0000-000000000001'::uuid],
                '{}'::jsonb
            )
            """
        )
        with pytest.raises(Exception):
            cur.execute(
                """
                INSERT INTO biography_conflict_resolutions (
                    fact_group_key, category, pin_ids, detection_snapshot, status
                ) VALUES (
                    'logistics|household|dinner',
                    'logistics',
                    ARRAY['00000000-0000-0000-0000-000000000002'::uuid],
                    '{}'::jsonb,
                    'open'
                )
                """
            )
        conn.rollback()


def test_migration_043_is_idempotent(schema) -> None:
    conn, schema_name = schema
    _apply_migration(conn, schema_name)
    _apply_migration(conn, schema_name)
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema_name}"')
        cur.execute(
            """
            SELECT COUNT(*) FROM pg_indexes
            WHERE schemaname = %s AND tablename = 'biography_conflict_resolutions'
            """,
            (schema_name,),
        )
        count_after_second = cur.fetchone()[0]
    assert count_after_second >= 3
