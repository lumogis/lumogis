# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Regression tests for migration 037-drop-users-refresh-token-jti (LUM-244).

Mirrors the live-Postgres migration-test precedent
(``test_proposal_queue_migration.py`` / ``test_credential_tiers_migration.py``):
``importorskip`` + a skip-on-unreachable fixture so the suite is a no-op
without a database and runs under ``compose-test-integration`` in CI.

Verifies the migration header's two claims: it drops the legacy column, and
it is idempotent / safe to re-apply (``DROP COLUMN IF EXISTS``).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MIGRATION_PATH = (
    _REPO_ROOT / "postgres" / "migrations" / "037-drop-users-refresh-token-jti.sql"
)


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
    name = f"test_mig037_{uuid.uuid4().hex[:12]}"
    conn = psycopg2.connect(**_conn_kwargs())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{name}"')
            cur.execute(f'SET search_path TO "{name}"')
            # Minimal pre-037 users table that still carries the legacy column
            # (the shape migration 010 created it with).
            cur.execute(
                """
                CREATE TABLE users (
                    id                TEXT PRIMARY KEY,
                    email             TEXT NOT NULL,
                    refresh_token_jti TEXT
                )
                """
            )
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


def _users_columns(conn, schema_name: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = %s AND table_name = 'users'
            """,
            (schema_name,),
        )
        return {r[0] for r in cur.fetchall()}


def test_migration_037_drops_refresh_token_jti_column(schema):
    conn, schema_name = schema
    assert "refresh_token_jti" in _users_columns(conn, schema_name)
    _apply_migration(conn, schema_name)
    cols = _users_columns(conn, schema_name)
    assert "refresh_token_jti" not in cols
    # Other columns are untouched.
    assert {"id", "email"} <= cols


def test_migration_037_is_idempotent(schema):
    conn, schema_name = schema
    _apply_migration(conn, schema_name)
    # Re-apply: DROP COLUMN IF EXISTS is a no-op the second time, no error.
    _apply_migration(conn, schema_name)
    assert "refresh_token_jti" not in _users_columns(conn, schema_name)


def test_migration_037_noop_when_column_already_absent(schema):
    conn, schema_name = schema
    # Drop it out-of-band, then the migration must still apply cleanly.
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema_name}"')
        cur.execute("ALTER TABLE users DROP COLUMN refresh_token_jti")
    _apply_migration(conn, schema_name)
    assert "refresh_token_jti" not in _users_columns(conn, schema_name)
