# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live Postgres: typed-edge reconcile retry budget (LUM-599) and its index (LUM-600).

``reconcile_entity_edges`` runs its real SQL against a real Postgres, in a
throwaway schema built from the shipped migrations (039 + 049 + 080). Only the
graph is faked, by a node-aware stand-in, so the tests control which edges
attach. The scan is global by design, so a private schema keeps it from
touching rows that belong to other tests.

Covers:
  * migration 080 is idempotent and lands the column and partial index;
  * an orphan edge ages out after GRAPH_RECONCILE_EDGE_MAX_ATTEMPTS misses and
    is then counted, not scanned, while a freshly-missed edge is still retried;
  * a successful projection resets the counter (the chosen policy);
  * the promotion reset gives an aged-out edge new retries;
  * under a LIMIT, fresh misses come before older ones;
  * EXPLAIN on a representative fixture (mostly stamped rows) shows the scan
    and the aged-out count both use ``idx_entity_edges_reconcile_stale``.

Skips when Postgres is unreachable (fails under LUMOGIS_REQUIRE_INTEGRATION_DB=1).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from tests.integration._pgguard import require_postgres

import config

psycopg2 = pytest.importorskip("psycopg2")
from psycopg2.extras import RealDictCursor  # noqa: E402

_MIGRATIONS = Path(__file__).resolve().parents[3] / "postgres" / "migrations"
_SCHEMA_MIGRATIONS = (
    "039-entity-edges.sql",
    "049-entity-edges-graph-projected-at.sql",
    "080-entity-edges-reconcile-backoff.sql",
)
_INDEX = "idx_entity_edges_reconcile_stale"
_MAX_ENV = "GRAPH_RECONCILE_EDGE_MAX_ATTEMPTS"


def _conn_kwargs() -> dict:
    return {
        "host": os.environ.get("POSTGRES_HOST", "postgres"),
        "port": int(os.environ.get("POSTGRES_PORT", "5432")),
        "user": os.environ.get("POSTGRES_USER", "lumogis"),
        "password": os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
        "dbname": os.environ.get("POSTGRES_DB", "lumogis"),
        "connect_timeout": 3,
    }


class _SchemaStore:
    """The MetadataStore surface reconcile uses, on one connection pinned to a schema."""

    def __init__(self, conn):
        self._conn = conn
        self.reads: list[tuple[str, object]] = []

    def ping(self):
        return True

    def fetch_all(self, query, params=None):
        self.reads.append((query, params))
        with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            return [dict(r) for r in cur.fetchall()]

    def fetch_one(self, query, params=None):
        self.reads.append((query, params))
        with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            return dict(row) if row else None

    def execute(self, query, params=None):
        with self._conn.cursor() as cur:
            cur.execute(query, params)

    def close(self):
        pass


class _Graph:
    """Node-aware graph stand-in: an edge attaches only when both nodes exist."""

    def __init__(self):
        self.nodes: set[tuple[str, str]] = set()

    def ping(self):
        return True

    def query(self, cypher, params=None):
        params = params or {}
        if "RETURN count(r) AS attached" in cypher:
            uid = params.get("user_id")
            both = (params.get("src"), uid) in self.nodes and (params.get("dst"), uid) in self.nodes
            return [{"attached": 1 if both else 0}]
        return []


@pytest.fixture
def schema_conn():
    try:
        conn = psycopg2.connect(**_conn_kwargs())
    except Exception as exc:  # noqa: BLE001 — diagnostic skip / CI failure
        require_postgres(exc)
    conn.autocommit = True
    name = f"test_lum599_{uuid.uuid4().hex[:12]}"
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{name}"')
            cur.execute(f'SET search_path TO "{name}"')
            for mig in _SCHEMA_MIGRATIONS:
                cur.execute((_MIGRATIONS / mig).read_text(encoding="utf-8"))
        yield conn, name
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        conn.close()


@pytest.fixture
def world(schema_conn, monkeypatch):
    """Reconcile wired to the schema store and a fake ``coding`` graph."""
    conn, _ = schema_conn
    ms = _SchemaStore(conn)
    graph = _Graph()
    key = config._graph_store_cache_key("coding")
    config._instances["metadata_store"] = ms
    config._instances[key] = graph
    monkeypatch.setenv(_MAX_ENV, "3")
    yield ms, graph
    config._instances.pop(key, None)


def _edge(ms, edge_id, *, src="s", dst="d", rel="IMPLEMENTS", attempts=0, stamped=False):
    ms.execute(
        "INSERT INTO entity_edges (id, user_id, bank, src_entity_id, dst_entity_id, "
        "relation_type, reconcile_attempts, graph_projected_at) "
        "VALUES (%s, 'u', 'coding', %s, %s, %s, %s, CASE WHEN %s THEN now() END)",
        (edge_id, src, dst, rel, attempts, stamped),
    )


def _state(ms, edge_id):
    return ms.fetch_one(
        "SELECT reconcile_attempts, graph_projected_at FROM entity_edges WHERE id = %s",
        (edge_id,),
    )


def _reconcile(limit=None):
    from plugins.graph.reconcile import reconcile_entity_edges

    return reconcile_entity_edges(limit=limit)


# ---------------------------------------------------------------------------
# Migration 080
# ---------------------------------------------------------------------------


def test_migration_080_is_idempotent_and_lands_column_and_index(schema_conn):
    conn, schema = schema_conn
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema}"')
        cur.execute(
            "INSERT INTO entity_edges (id, src_entity_id, dst_entity_id, relation_type) "
            "VALUES ('pre', 's', 'd', 'IMPLEMENTS')"
        )
        # A second application is a no-op.
        cur.execute((_MIGRATIONS / _SCHEMA_MIGRATIONS[-1]).read_text(encoding="utf-8"))
        cur.execute(
            "SELECT is_nullable, data_type, column_default FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'entity_edges' "
            "AND column_name = 'reconcile_attempts'",
            (schema,),
        )
        nullable, dtype, default = cur.fetchone()
        assert (nullable, dtype, default) == ("NO", "integer", "0")
        cur.execute("SELECT reconcile_attempts FROM entity_edges WHERE id = 'pre'")
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = %s AND indexname = %s",
            (schema, _INDEX),
        )
        rows = cur.fetchall()
        assert len(rows) == 1
        indexdef = rows[0][0]
        assert "(reconcile_attempts, id)" in indexdef
        assert "graph_projected_at IS NULL" in indexdef
        assert "valid_until IS NULL" in indexdef
        assert "RELATES_TO" in indexdef


# ---------------------------------------------------------------------------
# LUM-599 retry budget
# ---------------------------------------------------------------------------


def test_orphan_ages_out_while_fresh_miss_is_still_retried(world):
    ms, graph = world
    _edge(ms, "orphan", dst="never")  # dst node never materialises

    for expected in (1, 2, 3):
        result = _reconcile()
        assert result["scanned"] == 1
        assert result["skipped"] == 1
        assert _state(ms, "orphan")["reconcile_attempts"] == expected

    # Budget spent (max=3): no longer scanned, counted as aged out, untouched.
    result = _reconcile()
    assert result["scanned"] == 0
    assert result["aged_out"] == 1
    assert _state(ms, "orphan")["reconcile_attempts"] == 3

    # A freshly-missed edge is still retried next to the aged-out orphan...
    _edge(ms, "fresh", src="s2", dst="d2")
    result = _reconcile()
    assert result["scanned"] == 1
    assert result["aged_out"] == 1
    assert _state(ms, "fresh")["reconcile_attempts"] == 1

    # ...and attaches once its nodes land.
    graph.nodes |= {("s2", "u"), ("d2", "u")}
    result = _reconcile()
    assert result["stamped"] == 1
    fresh = _state(ms, "fresh")
    assert fresh["graph_projected_at"] is not None
    assert fresh["reconcile_attempts"] == 0


def test_successful_projection_resets_counter(world):
    """Policy: an attach zeroes the counter, so a later un-stamp (merge repoint)
    starts the edge on a full budget instead of its old spent one."""
    ms, graph = world
    _edge(ms, "e", attempts=2)  # one miss left
    graph.nodes |= {("s", "u"), ("d", "u")}

    assert _reconcile()["stamped"] == 1
    assert _state(ms, "e")["reconcile_attempts"] == 0

    # Un-stamp it as a merge would, with the nodes gone again: it gets 3 more tries.
    ms.execute("UPDATE entity_edges SET graph_projected_at = NULL WHERE id = 'e'")
    graph.nodes.clear()
    for _ in range(3):
        assert _reconcile()["scanned"] == 1
    assert _reconcile()["scanned"] == 0


def test_promotion_reset_revives_aged_out_edge(world):
    from services.entity_edges import reset_edge_reconcile_attempts_for_entity

    ms, graph = world
    _edge(ms, "to-staged", dst="staged", attempts=3)
    _edge(ms, "other", src="x", dst="y", attempts=3)
    assert _reconcile()["scanned"] == 0

    reset_edge_reconcile_attempts_for_entity("staged", user_id="u", ms=ms)
    assert _state(ms, "to-staged")["reconcile_attempts"] == 0
    assert _state(ms, "other")["reconcile_attempts"] == 3  # unrelated edge untouched

    graph.nodes |= {("s", "u"), ("staged", "u")}
    result = _reconcile()
    assert result["scanned"] == 1
    assert result["stamped"] == 1


def test_limit_takes_fresh_misses_first(world):
    ms, _ = world
    for i in range(5):
        _edge(ms, f"old-{i}", src=f"o{i}", attempts=2)
    _edge(ms, "zz-fresh", src="f")  # sorts last by id, first by attempts

    _reconcile(limit=1)

    assert _state(ms, "zz-fresh")["reconcile_attempts"] == 1
    assert all(_state(ms, f"old-{i}")["reconcile_attempts"] == 2 for i in range(5))


# ---------------------------------------------------------------------------
# LUM-600 partial index
# ---------------------------------------------------------------------------


def test_reconcile_scan_uses_partial_index(world, schema_conn):
    """Representative steady state: 50k stamped rows, a few dozen pending,
    some aged out, plus archived and RELATES_TO rows the scan must ignore."""
    ms, _ = world
    conn, _schema = schema_conn
    ms.execute(
        "INSERT INTO entity_edges (id, user_id, bank, src_entity_id, dst_entity_id, "
        "relation_type, graph_projected_at) "
        "SELECT 'stamped-' || g, 'u', 'coding', 's' || g, 'd' || g, 'IMPLEMENTS', now() "
        "FROM generate_series(1, 50000) g"
    )
    ms.execute(
        "INSERT INTO entity_edges (id, user_id, bank, src_entity_id, dst_entity_id, "
        "relation_type, reconcile_attempts, valid_until) "
        "SELECT 'pending-' || g, 'u', 'coding', 'ps' || g, 'pd' || g, "
        "  CASE WHEN g % 10 = 0 THEN 'RELATES_TO' ELSE 'IMPLEMENTS' END, "
        "  CASE WHEN g <= 20 THEN 0 ELSE 5 END, "
        "  CASE WHEN g % 7 = 0 THEN now() END "
        "FROM generate_series(1, 60) g"
    )
    ms.execute("ANALYZE entity_edges")

    # Capture the exact SQL reconcile issues, then EXPLAIN it with the same params.
    ms.reads.clear()
    _reconcile(limit=500)
    scan_sql, scan_params = next((q, p) for q, p in ms.reads if "ORDER BY reconcile_attempts" in q)
    count_sql, count_params = next((q, p) for q, p in ms.reads if "count(*)" in q)

    plans = {}
    with conn.cursor() as cur:
        for label, sql, params in (
            ("scan", scan_sql, scan_params),
            ("aged_out_count", count_sql, count_params),
        ):
            cur.execute("EXPLAIN " + sql, params)
            plans[label] = "\n".join(r[0] for r in cur.fetchall())
            print(f"\n--- EXPLAIN {label} ---\n{plans[label]}")  # evidence in -s runs

    for label, plan in plans.items():
        assert _INDEX in plan, f"{label} does not use {_INDEX}:\n{plan}"
        assert "Seq Scan" not in plan, f"{label} fell back to a sequential scan:\n{plan}"
