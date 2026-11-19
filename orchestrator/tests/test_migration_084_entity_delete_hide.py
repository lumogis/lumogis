# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Migration 084: block name rules, per-member Hide, delete tombstones (LUM-966).

Two halves. The static half reads the SQL text (it always runs). The live half
applies 082 + 084 to a throwaway database and proves the constraints accept what
the service writes and refuse what it must never write; it skips, like the other
migration regressions here, when no Postgres is reachable. ``postgres/init.sql``
must carry the same final shapes as the migration (a fresh install never runs it).
"""

# ruff: noqa: E501  (SQL literals)

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent
_MIG = _REPO / "postgres" / "migrations" / "084-entity-delete-hide.sql"
_INIT = _REPO / "postgres" / "init.sql"
_ENTITIES_STUB = "CREATE TABLE entities (entity_id UUID PRIMARY KEY, user_id TEXT)"


def _strip_comments(sql: str) -> str:
    return " ".join(re.sub(r"--[^\n]*", "", sql).split())


# ── static ───────────────────────────────────────────────────────────────────


def test_the_migration_is_idempotent_by_construction():
    sql = _strip_comments(_MIG.read_text())
    assert "DROP CONSTRAINT IF EXISTS entity_name_rules_action" in sql
    assert sql.count("ADD COLUMN IF NOT EXISTS") == 3
    assert "CREATE TABLE IF NOT EXISTS entity_hidden" in sql
    assert "CREATE TABLE IF NOT EXISTS entity_deletions" in sql
    assert "CREATE INDEX IF NOT EXISTS entity_deletions_pending_idx" in sql


def test_hide_cascades_with_the_owners_row_and_deletions_has_no_fk():
    sql = _strip_comments(_MIG.read_text())
    assert "REFERENCES entities(entity_id) ON DELETE CASCADE" in sql
    deletions = sql[sql.index("CREATE TABLE IF NOT EXISTS entity_deletions") :]
    assert "REFERENCES" not in deletions.split(");")[0]  # a tombstone outlives the row


def test_init_sql_carries_the_migrations_final_shapes():
    init = _strip_comments(_INIT.read_text())
    for fragment in (
        "CONSTRAINT entity_name_rules_action CHECK (action IN ('retype', 'block'))",
        "restore_to_type TEXT",
        "display_name TEXT",
        "document_evidence TEXT[] NOT NULL DEFAULT '{}'",
        "CONSTRAINT entity_name_rules_restore",
        "CONSTRAINT entity_name_rules_evidence_cap CHECK (cardinality(document_evidence) <= 500)",
        "CREATE TABLE IF NOT EXISTS entity_hidden",
        "CREATE TABLE IF NOT EXISTS entity_deletions",
        "entity_deletions_pending_idx",
    ):
        assert fragment in init, fragment


# ── live ─────────────────────────────────────────────────────────────────────

psycopg2 = pytest.importorskip("psycopg2")


def _kwargs(dbname=None):
    return {
        "host": os.environ.get("POSTGRES_HOST", "postgres"),
        "port": int(os.environ.get("POSTGRES_PORT", "5432")),
        "user": os.environ.get("POSTGRES_USER", "lumogis"),
        "password": os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
        "dbname": dbname or os.environ.get("POSTGRES_DB", "lumogis"),
        "connect_timeout": 3,
    }


@pytest.fixture(scope="module")
def db():
    try:
        admin = psycopg2.connect(**_kwargs())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable for the migration 084 test: {exc}")
    admin.autocommit = True
    name = f"t084_{uuid.uuid4().hex[:12]}"
    try:
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{name}"')
    except Exception as exc:  # noqa: BLE001
        admin.close()
        pytest.skip(f"cannot create a test database: {exc}")
    conn = psycopg2.connect(**_kwargs(name))
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(_ENTITIES_STUB)
            cur.execute((_REPO / "postgres/migrations/082-entity-name-rules.sql").read_text())
            cur.execute(_MIG.read_text())
            cur.execute(_MIG.read_text())  # idempotent: a second run is a no-op
        yield conn
    finally:
        conn.close()
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def _insert(conn, **kw):
    cols = {
        "user_id": "alice",
        "name_norm": f"n{uuid.uuid4().hex[:6]}",
        "from_type": "PERSON",
        "action": "block",
        "to_type": None,
        "restore_to_type": None,
        "document_evidence": [],
    }
    cols.update(kw)
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO entity_name_rules ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
            list(cols.values()),
        )


def test_accepts_a_block_a_converted_block_and_a_retype(db):
    _insert(db)
    _insert(db, from_type="ORG", restore_to_type="PERSON")
    _insert(db, action="retype", to_type="ORG")


@pytest.mark.parametrize(
    "kw",
    [
        {"to_type": "ORG"},  # a block has no target
        {"restore_to_type": "PERSON"},  # a restore may not equal its own kind
        {"action": "retype", "to_type": "ORG", "restore_to_type": "PERSON"},  # only blocks restore
        {"action": "other"},
        {"document_evidence": [str(i) for i in range(501)]},
    ],
)
def test_refuses_what_the_service_must_never_write(db, kw):
    with pytest.raises(psycopg2.errors.CheckViolation):
        _insert(db, **kw)


def test_evidence_at_the_cap_is_accepted(db):
    _insert(db, document_evidence=[str(i) for i in range(500)])


def test_hide_cascades_when_the_owner_row_goes(db):
    eid = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute("INSERT INTO entities (entity_id, user_id) VALUES (%s, 'alice')", (eid,))
        cur.execute("INSERT INTO entity_hidden (user_id, entity_key) VALUES ('bob', %s)", (eid,))
        cur.execute(
            "INSERT INTO entity_hidden (user_id, entity_key) VALUES ('bob', %s) ON CONFLICT DO NOTHING",
            (eid,),
        )
        cur.execute("SELECT count(*) FROM entity_hidden WHERE entity_key = %s", (eid,))
        assert cur.fetchone()[0] == 1
        cur.execute("DELETE FROM entities WHERE entity_id = %s", (eid,))
        cur.execute("SELECT count(*) FROM entity_hidden WHERE entity_key = %s", (eid,))
        assert cur.fetchone()[0] == 0
        with pytest.raises(psycopg2.errors.ForeignKeyViolation):
            cur.execute(
                "INSERT INTO entity_hidden (user_id, entity_key) VALUES ('bob', %s)",
                (str(uuid.uuid4()),),
            )


def test_a_tombstone_outlives_the_entity_row(db):
    eid = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO entity_deletions (entity_id, user_id, projection_ids) VALUES (%s, 'alice', %s::uuid[])",
            (eid, [str(uuid.uuid4())]),
        )
        cur.execute(
            "SELECT attempts, points_cleared_at, graph_cleared_at FROM entity_deletions WHERE entity_id = %s",
            (eid,),
        )
        assert cur.fetchone() == (0, None, None)
