# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""PostgreSQL regression: migration ``023-auth-sessions-and-token-version.sql``.

Ensures ``auth_sessions`` lands without mutating migration ``003``'s ``sessions``
(chat/memory table) — naming collision is a silent failure mode (LUM-29).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION_PATH = _REPO_ROOT / "postgres" / "migrations" / "023-auth-sessions-and-token-version.sql"


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
    name = f"test_mig029_{uuid.uuid4().hex[:12]}"
    conn = psycopg2.connect(**_conn_kwargs())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{name}"')
            cur.execute(f'SET search_path TO "{name}"')
            cur.execute(
                """
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    refresh_token_jti TEXT
                );
                """
            )
        sql = _MIGRATION_PATH.read_text(encoding="utf-8")
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{name}"')
            cur.execute(sql)

        yield conn, name
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        conn.close()


def test_migration_023_auth_sessions_and_preserves_sessions_table(isolated_schema):
    conn, schema = isolated_schema
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema}"')
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = 'auth_sessions'
            ORDER BY ordinal_position
            """,
            (schema,),
        )
        cols = [r[0] for r in cur.fetchall()]
        assert "user_id" in cols
        assert "family_id" in cols
        assert "refresh_token_hash" in cols
        cur.execute(f'SET search_path TO "{schema}"')
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = %s AND table_name = 'sessions'
            """,
            (schema,),
        )
        legacy = [r[0] for r in cur.fetchall()]
        assert legacy == ["id"]
