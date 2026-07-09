# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""PostgreSQL regression: migration ``049-entity-edges-graph-projected-at.sql`` (LUM-580).

Ensures the ``entity_edges.graph_projected_at`` reconcile stamp lands nullable,
is idempotent (``ADD COLUMN IF NOT EXISTS`` re-runs as a no-op), leaves existing
rows reading NULL (so the first reconcile pass self-heals them), and round-trips
a timestamp. Skips when no Postgres is reachable.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION_PATH = _REPO_ROOT / "postgres" / "migrations" / "049-entity-edges-graph-projected-at.sql"


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
    name = f"test_mig049_{uuid.uuid4().hex[:12]}"
    conn = psycopg2.connect(**_conn_kwargs())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{name}"')
            cur.execute(f'SET search_path TO "{name}"')
            # Minimal entity_edges shape (mirrors migration 039's columns used here).
            cur.execute(
                """
                CREATE TABLE entity_edges (
                    id             TEXT PRIMARY KEY,
                    user_id        TEXT NOT NULL DEFAULT 'default',
                    bank           TEXT NOT NULL DEFAULT 'coding',
                    src_entity_id  TEXT NOT NULL,
                    dst_entity_id  TEXT NOT NULL,
                    relation_type  TEXT NOT NULL,
                    valid_until    TIMESTAMPTZ
                );
                """
            )
            cur.execute(
                "INSERT INTO entity_edges (id, src_entity_id, dst_entity_id, relation_type) "
                "VALUES ('e1', 's', 'd', 'IMPLEMENTS')"
            )
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


def test_migration_049_adds_nullable_graph_projected_at(isolated_schema):
    conn, schema = isolated_schema
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema}"')
        cur.execute(
            """
            SELECT is_nullable, data_type
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = 'entity_edges'
              AND column_name = 'graph_projected_at'
            """,
            (schema,),
        )
        row = cur.fetchone()
        assert row is not None, "graph_projected_at column missing after migration"
        assert row[0] == "YES"  # nullable
        assert row[1] in ("timestamp with time zone",)

        # Existing rows read NULL -> picked up by the first reconcile pass.
        cur.execute(f'SET search_path TO "{schema}"')
        cur.execute("SELECT graph_projected_at FROM entity_edges WHERE id = 'e1'")
        assert cur.fetchone()[0] is None

        # Round-trips a stamp.
        cur.execute("UPDATE entity_edges SET graph_projected_at = now() WHERE id = 'e1'")
        cur.execute("SELECT graph_projected_at FROM entity_edges WHERE id = 'e1'")
        assert cur.fetchone()[0] is not None
