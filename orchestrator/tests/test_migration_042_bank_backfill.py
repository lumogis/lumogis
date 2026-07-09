# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Regression tests for migration 042-bank-backfill-personal (LUM-293)."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MIGRATION_PATH = _REPO_ROOT / "postgres" / "migrations" / "042-bank-backfill-personal.sql"


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
    name = f"test_mig042_{uuid.uuid4().hex[:12]}"
    conn = psycopg2.connect(**_conn_kwargs())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{name}"')
            cur.execute(f'SET search_path TO "{name}"')
            cur.execute(
                """
                CREATE TABLE memories (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    bank TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT[] NOT NULL DEFAULT '{}',
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    valid_from TIMESTAMPTZ NOT NULL DEFAULT now(),
                    valid_until TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE entity_edges (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    bank TEXT NOT NULL,
                    src_entity_id TEXT NOT NULL,
                    dst_entity_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    evidence_id TEXT,
                    valid_from TIMESTAMPTZ NOT NULL DEFAULT now(),
                    valid_until TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
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


def _seed(conn, schema_name: str) -> None:
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema_name}"')
        cur.execute(
            """
            INSERT INTO memories (id, user_id, bank, content, metadata) VALUES
              ('m-mcp', 'u', 'coding', 'mcp row', '{"source":"mcp"}'::jsonb),
              ('m-leg', 'u', 'coding', 'legacy row', '{}'::jsonb)
            """
        )
        cur.execute(
            """
            INSERT INTO entity_edges
              (id, user_id, bank, src_entity_id, dst_entity_id, relation_type, evidence_id)
            VALUES
              ('e-mcp', 'u', 'coding', 's', 'd', 'RELATES_TO', 'm-mcp'),
              ('e-leg', 'u', 'coding', 's', 'd', 'RELATES_TO', 'm-leg'),
              ('e-null', 'u', 'coding', 's', 'd', 'RELATES_TO', NULL)
            """
        )


def test_migration_042_preserves_mcp_coding_rows(schema):
    conn, schema_name = schema
    _seed(conn, schema_name)
    _apply_migration(conn, schema_name)
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema_name}"')
        cur.execute("SELECT id, bank FROM memories ORDER BY id")
        rows = {r[0]: r[1] for r in cur.fetchall()}
    assert rows["m-mcp"] == "coding"
    assert rows["m-leg"] == "personal"


def test_migration_042_null_evidence_edge_stays_coding(schema):
    conn, schema_name = schema
    _seed(conn, schema_name)
    _apply_migration(conn, schema_name)
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema_name}"')
        cur.execute("SELECT bank FROM entity_edges WHERE id = 'e-null'")
        assert cur.fetchone()[0] == "coding"


def test_migration_042_is_idempotent(schema):
    conn, schema_name = schema
    _seed(conn, schema_name)
    _apply_migration(conn, schema_name)
    _apply_migration(conn, schema_name)
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema_name}"')
        cur.execute("SELECT bank FROM memories WHERE id = 'm-leg'")
        assert cur.fetchone()[0] == "personal"
