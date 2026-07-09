# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""PostgreSQL regression: migration ``048-users-display-name.sql`` (LUM-585).

Ensures the admin-managed ``users.display_name`` column lands nullable, is
idempotent (``ADD COLUMN IF NOT EXISTS`` re-runs as a no-op), and does not
disturb existing ``users`` reads. Skips when no Postgres is reachable.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION_PATH = _REPO_ROOT / "postgres" / "migrations" / "048-users-display-name.sql"


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
        pytest.skip(f"Postgres not reachable for migration regression test: {exc}")
    conn.close()
    return True


@pytest.fixture
def isolated_schema(_pg_available):
    name = f"test_mig048_{uuid.uuid4().hex[:12]}"
    conn = psycopg2.connect(**_conn_kwargs())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{name}"')
            cur.execute(f'SET search_path TO "{name}"')
            cur.execute(
                """
                CREATE TABLE users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL
                );
                """
            )
            cur.execute("INSERT INTO users (id, email) VALUES ('u1', 'a@home.lan')")
        sql = _MIGRATION_PATH.read_text(encoding="utf-8")
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{name}"')
            cur.execute(sql)
            # Idempotent: applying a second time is a no-op.
            cur.execute(sql)
        yield conn, name
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        conn.close()


def test_migration_048_adds_nullable_display_name(isolated_schema):
    conn, schema = isolated_schema
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema}"')
        cur.execute(
            """
            SELECT is_nullable, data_type
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = 'users' AND column_name = 'display_name'
            """,
            (schema,),
        )
        row = cur.fetchone()
        assert row is not None, "display_name column missing after migration"
        assert row[0] == "YES"  # nullable
        assert row[1] == "text"

        # Existing rows are undisturbed and default to NULL.
        cur.execute(f'SET search_path TO "{schema}"')
        cur.execute("SELECT display_name FROM users WHERE id = 'u1'")
        assert cur.fetchone()[0] is None

        # Round-trips a value.
        cur.execute("UPDATE users SET display_name = 'Alex' WHERE id = 'u1'")
        cur.execute("SELECT display_name FROM users WHERE id = 'u1'")
        assert cur.fetchone()[0] == "Alex"
