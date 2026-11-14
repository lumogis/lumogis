# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""PostgreSQL regression: migration ``026-entities-memory-type.sql`` (LUM-390).

Migration 026 adds ``entities.memory_type`` and ``entities.last_verified_at`` with
a CHECK constraint restricting the classification to a closed set. Nothing executed
that SQL in test before this module — a schema claim nobody had checked, and the
CHECK constraint in particular is the kind of thing that is silently wrong until
production data violates it.

Why this lives in ``tests/integration/`` (LUM-390 asked for the choice to be stated)
------------------------------------------------------------------------------------
Directly alongside :mod:`tests.integration.test_auth_sessions_migration`, which is
the same shape of harness against the same backend. The LUM-753 membership rule for
this directory — "a test here requires a real external backend" — is satisfied: this
module is meaningless without a live Postgres. Placement also buys the ``integration``
marker, the LUM-718 terminal skip reporter, and inclusion in
``make check-integration-hermeticity``.

Note the guard is imported and called **explicitly** below. There is no autouse
fixture in this directory's ``conftest.py`` that reaches Postgres; it registers
markers and reporting only. A module that assumed otherwise would skip silently,
which is the exact defect LUM-718 and LUM-788 exist to end.

The vacuity trap
----------------
``postgres/init.sql`` **already** declares both columns inline — 026 exists to
upgrade installs predating it. So a fixture seeding ``entities`` from ``init.sql``
would make ``ADD COLUMN IF NOT EXISTS`` a no-op and every assertion here would pass
without the migration doing anything.

:func:`isolated_schema` therefore builds a deliberately **pre-026** ``entities``
table, and :func:`test_migration_026_adds_both_columns` asserts the columns are
ABSENT before applying. Without that pre-assertion the suite cannot distinguish
"the migration added them" from "they were always there".
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from tests.integration._pgguard import require_postgres

psycopg2 = pytest.importorskip("psycopg2")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION_PATH = _REPO_ROOT / "postgres" / "migrations" / "026-entities-memory-type.sql"

#: The closed set migration 026's CHECK constraint permits, plus NULL which it
#: explicitly allows (``memory_type IS NULL OR memory_type IN (...)``).
_VALID_MEMORY_TYPES = ("user_preference", "correction", "relationship")

#: Columns 026 claims to add. Asserted absent before, present after.
_ADDED_COLUMNS = ("memory_type", "last_verified_at")


def _conn_kwargs() -> dict:
    """Connection settings from the ``POSTGRES_*`` convention.

    Migration harnesses read these rather than ``LUMOGIS_INTEGRATION_POSTGRES_DSN``.
    The two are not interchangeable and setting only the DSN makes this module fail
    with ``could not translate host name "postgres"`` — that is the LUM-718 guard
    working correctly, not a broken test (LUM-788 owns unifying them).
    """
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
    except Exception as exc:  # noqa: BLE001 — routed through the LUM-718 guard
        require_postgres(exc)
    conn.close()
    return True


@pytest.fixture
def isolated_schema(_pg_available):
    """A throwaway schema holding a **pre-026** ``entities`` table.

    Only the columns 026 needs to exist are declared: ``user_id`` because the
    partial index is on ``(user_id, memory_type)``, and a primary key so rows can
    be inserted. ``memory_type`` and ``last_verified_at`` are deliberately absent —
    see the module docstring.
    """
    name = f"test_mig026_{uuid.uuid4().hex[:12]}"
    conn = psycopg2.connect(**_conn_kwargs())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{name}"')
            cur.execute(f'SET search_path TO "{name}"')
            cur.execute(
                """
                CREATE TABLE entities (
                    entity_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    name        TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    user_id     TEXT NOT NULL DEFAULT 'default'
                );
                """
            )
        yield conn, name
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        conn.close()


def _apply_migration(conn, schema_name: str) -> None:
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema_name}"')
        cur.execute(_MIGRATION_PATH.read_text(encoding="utf-8"))


def _columns(conn, schema_name: str) -> dict[str, tuple[str, str]]:
    """``{column_name: (data_type, is_nullable)}`` for ``entities``."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = 'entities'
            """,
            (schema_name,),
        )
        return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


def _insert(cur, memory_type):
    cur.execute(
        "INSERT INTO entities (name, entity_type, memory_type) VALUES (%s, %s, %s)",
        ("n", "CONCEPT", memory_type),
    )


def test_migration_026_adds_both_columns(isolated_schema):
    """The columns are absent before and present after, with the claimed types.

    The pre-assertion is load-bearing: ``init.sql`` declares both columns inline, so
    against a schema built from it this migration is a no-op and a post-only
    assertion would pass while proving nothing.
    """
    conn, schema_name = isolated_schema

    before = _columns(conn, schema_name)
    for col in _ADDED_COLUMNS:
        assert col not in before, (
            f"{col} present BEFORE migration 026 — the fixture is seeding a "
            "post-migration schema and every assertion here would be vacuous"
        )

    _apply_migration(conn, schema_name)

    after = _columns(conn, schema_name)
    assert after["memory_type"] == ("text", "YES")
    assert after["last_verified_at"] == ("timestamp with time zone", "YES")


def test_migration_026_check_rejects_out_of_domain_memory_type(isolated_schema):
    """An out-of-domain value is refused by the CHECK constraint.

    This is the assertion the ticket calls out: a test that only inserts valid rows
    proves nothing about a constraint's existence.
    """
    from psycopg2 import errors as pg_errors

    conn, schema_name = isolated_schema
    _apply_migration(conn, schema_name)
    with pytest.raises(pg_errors.CheckViolation):
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema_name}"')
            _insert(cur, "nonsense")


@pytest.mark.parametrize("memory_type", [*_VALID_MEMORY_TYPES, None])
def test_migration_026_check_accepts_each_valid_value(isolated_schema, memory_type):
    """Every permitted value is accepted — including NULL.

    NULL is parametrised deliberately: the constraint reads ``memory_type IS NULL OR
    memory_type IN (...)``, so dropping the NULL branch is a real mutation that no
    amount of testing the three literals would catch.
    """
    conn, schema_name = isolated_schema
    _apply_migration(conn, schema_name)
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema_name}"')
        _insert(cur, memory_type)
        cur.execute(
            "SELECT COUNT(*) FROM entities WHERE memory_type IS NOT DISTINCT FROM %s",
            (memory_type,),
        )
        assert cur.fetchone()[0] == 1


def test_migration_026_is_idempotent(isolated_schema):
    """Re-applying does not error — the house pattern for every migration harness.

    Non-trivial here: 026 does ``DROP CONSTRAINT IF EXISTS`` then ``ADD CONSTRAINT``
    unconditionally, so a second run re-creates the constraint rather than skipping
    it. Without the DROP the second apply would fail on a duplicate constraint name.
    """
    conn, schema_name = isolated_schema
    _apply_migration(conn, schema_name)
    _apply_migration(conn, schema_name)

    after = _columns(conn, schema_name)
    for col in _ADDED_COLUMNS:
        assert col in after
    # And the constraint still enforces after the drop/re-add cycle.
    from psycopg2 import errors as pg_errors

    with pytest.raises(pg_errors.CheckViolation):
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema_name}"')
            _insert(cur, "nonsense")


def test_migration_026_creates_correction_partial_index(isolated_schema):
    """The partial index on ``(user_id, memory_type) WHERE memory_type = 'correction'``.

    Asserted on the stored index definition rather than only the name, so an index
    created over the right columns but without the partial predicate — a different
    and much larger index — does not pass as equivalent.
    """
    conn, schema_name = isolated_schema
    _apply_migration(conn, schema_name)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT indexdef FROM pg_indexes
            WHERE schemaname = %s AND tablename = 'entities'
              AND indexname = 'idx_entities_user_memory_correction'
            """,
            (schema_name,),
        )
        row = cur.fetchone()
    assert row is not None, "migration 026 must create idx_entities_user_memory_correction"
    indexdef = row[0]
    assert "user_id" in indexdef and "memory_type" in indexdef
    assert "WHERE (memory_type = 'correction'::text)" in indexdef, indexdef
