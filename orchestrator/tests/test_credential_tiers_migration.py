# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Regression tests for migration 018-household-and-instance-system-connector-credentials.

Pinned by ADR ``credential_scopes_shared_system`` and the implementation
plan §"Test cases / Migration-runner regression" (cases 52b–52g).

Mirrors the precedent set by ``test_per_user_connector_permissions_migration.py``
for migration 016 (D6.5 acceptance).

These tests exercise the migration body against a real PostgreSQL
instance. They skip when no Postgres is reachable so the unit-test
suite on a developer laptop without Docker still runs green; in CI /
the ``docker compose -f docker-compose.test.yml`` flow Postgres is up
and the tests run.

Each test sets up an isolated schema (``test_mig018_<uuid>``) so
parallel runs do not collide.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MIGRATION_PATH = (
    _REPO_ROOT
    / "postgres"
    / "migrations"
    / "018-household-and-instance-system-connector-credentials.sql"
)


_HOUSEHOLD_TABLE = "household_connector_credentials"
_SYSTEM_TABLE = "instance_system_connector_credentials"
_EXPECTED_COLUMNS = {
    "connector",
    "ciphertext",
    "key_version",
    "created_at",
    "updated_at",
    "created_by",
    "updated_by",
}


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
def schema(_pg_available):
    """Create an isolated test schema. Yields ``(conn, schema_name)``."""
    name = f"test_mig018_{uuid.uuid4().hex[:12]}"
    conn = psycopg2.connect(**_conn_kwargs())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{name}"')
            cur.execute(f'SET search_path TO "{name}"')
        yield conn, name
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA "{name}" CASCADE')
        conn.close()


def _apply_migration(conn, schema_name: str) -> None:
    sql = _MIGRATION_PATH.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema_name}"')
        cur.execute(sql)


def _table_exists(conn, schema_name: str, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
            (schema_name, table_name),
        )
        return cur.fetchone() is not None


def _column_set(conn, schema_name: str, table_name: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s",
            (schema_name, table_name),
        )
        return {row[0] for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# Test #52b — clean apply on fresh DB.
# ---------------------------------------------------------------------------


def test_migration_018_applies_cleanly_on_fresh_db(schema):
    conn, schema_name = schema

    _apply_migration(conn, schema_name)

    assert _table_exists(conn, schema_name, _HOUSEHOLD_TABLE)
    assert _table_exists(conn, schema_name, _SYSTEM_TABLE)


# ---------------------------------------------------------------------------
# Test #52c — idempotency on re-apply.
# ---------------------------------------------------------------------------


def test_migration_018_is_idempotent_on_reapply(schema):
    conn, schema_name = schema

    _apply_migration(conn, schema_name)
    cols_first_household = _column_set(conn, schema_name, _HOUSEHOLD_TABLE)
    cols_first_system = _column_set(conn, schema_name, _SYSTEM_TABLE)

    _apply_migration(conn, schema_name)
    cols_second_household = _column_set(conn, schema_name, _HOUSEHOLD_TABLE)
    cols_second_system = _column_set(conn, schema_name, _SYSTEM_TABLE)

    assert cols_first_household == cols_second_household
    assert cols_first_system == cols_second_system


# ---------------------------------------------------------------------------
# Test #52d — expected columns per table.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table_name", [_HOUSEHOLD_TABLE, _SYSTEM_TABLE])
def test_migration_018_creates_expected_columns_per_table(schema, table_name):
    conn, schema_name = schema

    _apply_migration(conn, schema_name)
    cols = _column_set(conn, schema_name, table_name)

    assert cols == _EXPECTED_COLUMNS, (
        f"Column set drift for {table_name}: "
        f"missing={_EXPECTED_COLUMNS - cols} "
        f"extra={cols - _EXPECTED_COLUMNS}"
    )


# ---------------------------------------------------------------------------
# Test #52e — CHECK constraint rejects 'self' actor.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table_name", [_HOUSEHOLD_TABLE, _SYSTEM_TABLE])
def test_migration_018_check_constraint_rejects_self_actor(
    schema,
    table_name,
):
    conn, schema_name = schema
    _apply_migration(conn, schema_name)

    from psycopg2 import errors as pg_errors

    with conn.cursor() as cur:
        with pytest.raises(pg_errors.CheckViolation):
            cur.execute(
                f"INSERT INTO {table_name} "
                "(connector, ciphertext, key_version, created_by, updated_by) "
                "VALUES (%s, %s, %s, %s, %s)",
                ("testconnector", b"x", 1, "self", "admin:alice"),
            )

    with conn.cursor() as cur:
        with pytest.raises(pg_errors.CheckViolation):
            cur.execute(
                f"INSERT INTO {table_name} "
                "(connector, ciphertext, key_version, created_by, updated_by) "
                "VALUES (%s, %s, %s, %s, %s)",
                ("testconnector", b"x", 1, "admin:alice", "self"),
            )


# ---------------------------------------------------------------------------
# Test #52f — CHECK constraint rejects bad connector format.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table_name", [_HOUSEHOLD_TABLE, _SYSTEM_TABLE])
def test_migration_018_check_constraint_rejects_bad_connector_format(
    schema,
    table_name,
):
    conn, schema_name = schema
    _apply_migration(conn, schema_name)

    from psycopg2 import errors as pg_errors

    with conn.cursor() as cur:
        with pytest.raises(pg_errors.CheckViolation):
            cur.execute(
                f"INSERT INTO {table_name} "
                "(connector, ciphertext, key_version, created_by, updated_by) "
                "VALUES (%s, %s, %s, %s, %s)",
                ("BAD CONNECTOR", b"x", 1, "system", "system"),
            )


# ---------------------------------------------------------------------------
# Test #52g — CHECK constraint accepts 'admin:<id>' and 'system' actors.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table_name", [_HOUSEHOLD_TABLE, _SYSTEM_TABLE])
def test_migration_018_check_constraint_accepts_admin_actor(
    schema,
    table_name,
):
    conn, schema_name = schema
    _apply_migration(conn, schema_name)

    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {table_name} "
            "(connector, ciphertext, key_version, created_by, updated_by) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("connector_one", b"x", 1, "admin:alice", "admin:alice"),
        )
        cur.execute(
            f"INSERT INTO {table_name} "
            "(connector, ciphertext, key_version, created_by, updated_by) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("connector_two", b"x", 1, "system", "system"),
        )
        cur.execute(f"SELECT COUNT(*) FROM {table_name}")
        (count,) = cur.fetchone()

    assert count == 2
