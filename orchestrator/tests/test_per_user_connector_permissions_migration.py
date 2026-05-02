# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Regression tests for migration 016-per-user-connector-permissions.

Plan: ``.cursor/plans/per_user_connector_permissions.plan.md`` §"Test cases"
/ "Migration regression". Audit A2 closure (per-user connector permissions
+ per-user routine_do_tracking).

These tests exercise the migration body against a real PostgreSQL
instance. They skip when no Postgres is reachable so the unit-test suite
on a developer laptop without Docker still runs green; in CI / the
``docker compose -f docker-compose.test.yml`` flow, Postgres is up and
the tests run.

Each test sets up an isolated schema (``test_mig016_<uuid>``) so parallel
runs do not collide and no cleanup is needed beyond DROP SCHEMA at the
end. The schema is created with the minimum subset of pre-016 tables
required (``users`` from migration 010, ``connector_permissions`` and
``routine_do_tracking`` from ``init.sql``) — the migration body is then
applied verbatim against that subset.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MIGRATION_PATH = _REPO_ROOT / "postgres" / "migrations" / "016-per-user-connector-permissions.sql"


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
    """Create an isolated test schema with the pre-016 baseline tables.

    Yields ``(conn, schema_name)``. Schema is dropped on teardown.
    """
    name = f"test_mig016_{uuid.uuid4().hex[:12]}"
    conn = psycopg2.connect(**_conn_kwargs())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{name}"')
            cur.execute(f'SET search_path TO "{name}"')
            # Minimum pre-016 schema needed to exercise migration 016.
            cur.execute(
                """
                CREATE TABLE users (
                    id              TEXT PRIMARY KEY,
                    email           TEXT NOT NULL UNIQUE,
                    password_hash   TEXT NOT NULL,
                    role            TEXT NOT NULL DEFAULT 'user',
                    disabled        BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE connector_permissions (
                    id              SERIAL PRIMARY KEY,
                    connector       TEXT NOT NULL,
                    mode            TEXT NOT NULL DEFAULT 'ASK',
                    user_id         TEXT NOT NULL DEFAULT 'default',
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CONSTRAINT connector_permissions_connector_key UNIQUE (connector)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE routine_do_tracking (
                    id              SERIAL PRIMARY KEY,
                    connector       TEXT NOT NULL,
                    action_type     TEXT NOT NULL,
                    approval_count  INTEGER NOT NULL DEFAULT 0,
                    edit_count      INTEGER NOT NULL DEFAULT 0,
                    auto_approved   BOOLEAN NOT NULL DEFAULT FALSE,
                    granted_at      TIMESTAMPTZ,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CONSTRAINT routine_do_tracking_connector_action_type_key
                        UNIQUE (connector, action_type)
                )
                """
            )
        yield conn, name
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA "{name}" CASCADE')
        conn.close()


def _apply_migration(conn, schema_name: str) -> None:
    """Apply 016 verbatim against ``schema_name``."""
    sql = _MIGRATION_PATH.read_text(encoding="utf-8")
    # The migration uses BEGIN / COMMIT explicitly; we run it inside our
    # own connection with autocommit True so the migration's BEGIN/COMMIT
    # delimit the transaction. We must SET search_path within the same
    # statement batch so the migration's DDL targets the test schema.
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema_name}"')
        cur.execute(sql)


def _seed_user(conn, *, user_id: str, email: str, disabled: bool = False) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, email, password_hash, disabled) "
            "VALUES (%s, %s, %s, %s)",
            (user_id, email, "x" * 32, disabled),
        )


def _seed_global_permission(conn, *, connector: str, mode: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO connector_permissions (user_id, connector, mode) "
            "VALUES (%s, %s, %s)",
            ("default", connector, mode),
        )


def _seed_global_routine(
    conn, *, connector: str, action_type: str,
    approval_count: int = 0, auto_approved: bool = False,
) -> None:
    """Insert a pre-016 'global' routine row.

    Pre-016 the routine_do_tracking schema has no user_id column, so the
    INSERT must NOT name it -- migration 016's Phase 2
    ``ADD COLUMN ... DEFAULT 'default'`` populates user_id retroactively.
    """
    with conn.cursor() as cur:
        if auto_approved:
            cur.execute(
                "INSERT INTO routine_do_tracking "
                "(connector, action_type, approval_count, auto_approved, granted_at) "
                "VALUES (%s, %s, %s, TRUE, NOW())",
                (connector, action_type, approval_count),
            )
        else:
            cur.execute(
                "INSERT INTO routine_do_tracking "
                "(connector, action_type, approval_count, auto_approved) "
                "VALUES (%s, %s, %s, FALSE)",
                (connector, action_type, approval_count),
            )


def _fetch_perm_rows(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT user_id, connector, mode FROM connector_permissions "
            "ORDER BY user_id, connector"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _fetch_routine_rows(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT user_id, connector, action_type, approval_count, auto_approved "
            "FROM routine_do_tracking ORDER BY user_id, connector, action_type"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Tests #35 — eager-backfill cross-joins existing users with non-ASK rows
# ---------------------------------------------------------------------------

def test_eager_backfill_cross_joins_existing_users_with_non_ask_global_rows(schema):
    conn, schema_name = schema
    _seed_user(conn, user_id="alice", email="alice@home.lan")
    _seed_user(conn, user_id="bob", email="bob@home.lan")
    _seed_global_permission(conn, connector="filesystem-mcp", mode="DO")

    _apply_migration(conn, schema_name)

    rows = _fetch_perm_rows(conn)
    pairs = {(r["user_id"], r["connector"], r["mode"]) for r in rows}
    assert ("alice", "filesystem-mcp", "DO") in pairs
    assert ("bob", "filesystem-mcp", "DO") in pairs
    # No legacy 'default' row remains.
    assert not any(r["user_id"] == "default" for r in rows)


# ---------------------------------------------------------------------------
# Test #36 — skip when only the default sentinel row exists
# ---------------------------------------------------------------------------

def test_eager_backfill_skips_when_only_default_user_exists(schema):
    conn, schema_name = schema
    # No real users seeded.
    _seed_global_permission(conn, connector="filesystem-mcp", mode="DO")

    _apply_migration(conn, schema_name)

    rows = _fetch_perm_rows(conn)
    # Legacy 'default' row remains for db_default_user_remap.py to remap.
    assert any(
        r["user_id"] == "default" and r["connector"] == "filesystem-mcp"
        for r in rows
    )


# ---------------------------------------------------------------------------
# Test #37 — eager backfill skips global ASK rows
# ---------------------------------------------------------------------------

def test_eager_backfill_skips_global_ASK_rows(schema):
    conn, schema_name = schema
    _seed_user(conn, user_id="alice", email="alice@home.lan")
    _seed_user(conn, user_id="bob", email="bob@home.lan")
    _seed_global_permission(conn, connector="filesystem-mcp", mode="ASK")

    _apply_migration(conn, schema_name)

    rows = _fetch_perm_rows(conn)
    # ASK rows are not fanned out (lazy fallback already returns ASK).
    assert not any(r["mode"] == "ASK" for r in rows), (
        f"Per-user ASK rows should not be created; got {rows!r}"
    )
    # Legacy 'default' row swept after fan-out (Phase 3 sweep).
    assert not any(r["user_id"] == "default" for r in rows)


# ---------------------------------------------------------------------------
# Test #38 — idempotency on re-apply
# ---------------------------------------------------------------------------

def test_migration_is_idempotent_on_reapply(schema):
    conn, schema_name = schema
    _seed_user(conn, user_id="alice", email="alice@home.lan")
    _seed_user(conn, user_id="bob", email="bob@home.lan")
    _seed_global_permission(conn, connector="filesystem-mcp", mode="DO")

    _apply_migration(conn, schema_name)
    rows_first = _fetch_perm_rows(conn)
    _apply_migration(conn, schema_name)
    rows_second = _fetch_perm_rows(conn)

    assert rows_first == rows_second, (
        "Re-applying the migration changed the row set"
    )


# ---------------------------------------------------------------------------
# Test #39 — user_id column exists with correct shape post-016
# ---------------------------------------------------------------------------

def test_migration_adds_user_id_column_to_routine_do_tracking(schema):
    conn, schema_name = schema
    _apply_migration(conn, schema_name)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s "
            "AND column_name = 'user_id'",
            (schema_name, "routine_do_tracking"),
        )
        row = cur.fetchone()
    assert row is not None, "user_id column missing from routine_do_tracking"
    assert row[0] == "text"
    assert row[1] == "NO"


# ---------------------------------------------------------------------------
# Test #40 — composite unique constraint accepts two per-user rows
# ---------------------------------------------------------------------------

def test_migration_swaps_unique_constraint_on_connector_permissions(schema):
    conn, schema_name = schema
    _seed_user(conn, user_id="alice", email="alice@home.lan")
    _seed_user(conn, user_id="bob", email="bob@home.lan")
    _apply_migration(conn, schema_name)

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO connector_permissions (user_id, connector, mode) "
            "VALUES (%s, %s, %s), (%s, %s, %s)",
            ("alice", "filesystem-mcp", "DO", "bob", "filesystem-mcp", "DO"),
        )
    rows = _fetch_perm_rows(conn)
    pairs = {(r["user_id"], r["connector"]) for r in rows}
    assert ("alice", "filesystem-mcp") in pairs
    assert ("bob", "filesystem-mcp") in pairs


# ---------------------------------------------------------------------------
# Test #41 — fans out to disabled users too (no silent ASK demotion)
# ---------------------------------------------------------------------------

def test_eager_backfill_fans_out_to_disabled_users_too(schema):
    conn, schema_name = schema
    _seed_user(conn, user_id="alice", email="alice@home.lan", disabled=False)
    _seed_user(conn, user_id="bob", email="bob@home.lan", disabled=True)
    _seed_global_permission(conn, connector="filesystem-mcp", mode="DO")

    _apply_migration(conn, schema_name)

    rows = _fetch_perm_rows(conn)
    pairs = {(r["user_id"], r["connector"], r["mode"]) for r in rows}
    assert ("alice", "filesystem-mcp", "DO") in pairs
    assert ("bob", "filesystem-mcp", "DO") in pairs, (
        "Disabled user MUST receive a per-user row to preserve "
        "pre-migration DO mode against future re-enable. The old "
        "WHERE disabled = FALSE gate broke this."
    )
    assert not any(r["user_id"] == "default" for r in rows)


# ---------------------------------------------------------------------------
# Test #42 — pending routine counters retained for db_default_user_remap.py
# ---------------------------------------------------------------------------

def test_eager_backfill_preserves_pending_routine_counters_via_remap(schema):
    conn, schema_name = schema
    _seed_user(conn, user_id="alice", email="alice@home.lan")
    _seed_user(conn, user_id="bob", email="bob@home.lan")
    _seed_user(conn, user_id="carol", email="carol@home.lan")
    _seed_global_routine(
        conn, connector="calendar-mcp", action_type="create_event",
        approval_count=15, auto_approved=True,
    )
    _seed_global_routine(
        conn, connector="filesystem-mcp", action_type="write_file",
        approval_count=14, auto_approved=False,
    )

    _apply_migration(conn, schema_name)

    rows = _fetch_routine_rows(conn)
    auto_approved_keys = {
        (r["user_id"], r["connector"], r["action_type"])
        for r in rows if r["auto_approved"]
    }
    # (a) all three users got the auto_approved=TRUE row.
    for uid in ("alice", "bob", "carol"):
        assert (uid, "calendar-mcp", "create_event") in auto_approved_keys, (
            f"Missing fan-out for {uid}"
        )
    # (b) the 'default' calendar/create_event row is swept.
    default_calendar = [
        r for r in rows
        if r["user_id"] == "default" and r["connector"] == "calendar-mcp"
    ]
    assert default_calendar == [], (
        "Auto-approved 'default' row must be swept after fan-out"
    )
    # (c) the 'default' filesystem/write_file pending row is RETAINED.
    default_pending = [
        r for r in rows
        if r["user_id"] == "default"
        and r["connector"] == "filesystem-mcp"
        and r["action_type"] == "write_file"
    ]
    assert len(default_pending) == 1
    assert default_pending[0]["approval_count"] == 14
    assert default_pending[0]["auto_approved"] is False
    # (d) no user has a per-user filesystem/write_file row yet.
    user_pending = [
        r for r in rows
        if r["user_id"] in {"alice", "bob", "carol"}
        and r["connector"] == "filesystem-mcp"
        and r["action_type"] == "write_file"
    ]
    assert user_pending == []


# ---------------------------------------------------------------------------
# Test #43 — empty users table → no fan-out, legacy rows retained
# ---------------------------------------------------------------------------

def test_eager_backfill_skips_when_users_table_is_empty(schema):
    conn, schema_name = schema
    _seed_global_permission(conn, connector="filesystem-mcp", mode="DO")
    _seed_global_routine(
        conn, connector="calendar-mcp", action_type="create_event",
        approval_count=20, auto_approved=True,
    )

    _apply_migration(conn, schema_name)

    perm_rows = _fetch_perm_rows(conn)
    routine_rows = _fetch_routine_rows(conn)

    assert any(r["user_id"] == "default" for r in perm_rows), (
        "EXISTS(users) gate must SKIP the sweep when no users exist"
    )
    assert any(r["user_id"] == "default" for r in routine_rows)


# ---------------------------------------------------------------------------
# Test #44 — idempotent after partial user growth
# ---------------------------------------------------------------------------

def test_migration_idempotency_after_partial_user_growth(schema):
    conn, schema_name = schema
    _seed_user(conn, user_id="alice", email="alice@home.lan")
    _seed_global_permission(conn, connector="filesystem-mcp", mode="DO")

    _apply_migration(conn, schema_name)
    rows_first = _fetch_perm_rows(conn)
    pairs_first = {(r["user_id"], r["connector"]) for r in rows_first}
    assert ("alice", "filesystem-mcp") in pairs_first

    # Add bob AFTER the first migration apply.
    _seed_user(conn, user_id="bob", email="bob@home.lan")

    _apply_migration(conn, schema_name)
    rows_second = _fetch_perm_rows(conn)
    pairs_second = {(r["user_id"], r["connector"]) for r in rows_second}

    # Alice's row unchanged. Bob does NOT pick up the row because the
    # legacy 'default' row was already swept on first apply.
    assert ("alice", "filesystem-mcp") in pairs_second
    assert ("bob", "filesystem-mcp") not in pairs_second
