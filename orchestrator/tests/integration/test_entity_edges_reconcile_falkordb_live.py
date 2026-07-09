# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live FalkorDB race→reconcile proof for typed ``entity_edges`` (LUM-597 / LUM-580).

Simulates the async-node / sync-edge race: an ``entity_edges`` row is written
while FalkorDB nodes are absent (``graph_projected_at`` stays NULL), nodes are
then MERGEd, and ``reconcile_entity_edges`` attaches the typed edge without
mutating the co-occurrence ``RELATES_TO`` aggregate slot.

Skips when FalkorDB is not configured. Primary gate:
``make compose-test-integration``.
"""

from __future__ import annotations

import os
import uuid

import pytest

import config
from plugins.graph.reconcile import reconcile_entity_edges

pytestmark = pytest.mark.integration


def _live_stores_or_skip(monkeypatch):
    url = os.environ.get("FALKORDB_URL", "redis://falkordb:6379")
    if not url:
        pytest.skip("FALKORDB_URL not set — run with docker-compose.falkordb.yml overlay")
    monkeypatch.setenv("GRAPH_BACKEND", "falkordb")
    monkeypatch.setenv("FALKORDB_URL", url)
    for key in list(config._instances.keys()):
        if key.startswith("graph_store:") or key == "graph_store":
            config._instances.pop(key, None)
    try:
        from adapters.postgres_store import PostgresStore

        ms = PostgresStore(
            host=os.environ.get("POSTGRES_HOST", "postgres"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            user=os.environ.get("POSTGRES_USER", "lumogis"),
            password=os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
            dbname=os.environ.get("POSTGRES_DB", "lumogis"),
        )
        if not ms.ping():
            raise RuntimeError("postgres ping failed")
        gs = config.get_graph_store("personal")
        if gs is None or not gs.ping():
            raise RuntimeError("FalkorDB graph store unavailable")
        return ms, gs
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"FalkorDB/Postgres live stack required — run make compose-test-integration: {exc}")


def _cleanup(ms, gs, edge_id: str, user_id: str, src: str, dst: str) -> None:
    try:
        gs.query(
            "MATCH (a {lumogis_id: $src, user_id: $uid})-[r:IMPLEMENTS]->(b {lumogis_id: $dst, user_id: $uid}) "
            "DELETE r",
            {"src": src, "dst": dst, "uid": user_id},
        )
        gs.query(
            "MATCH (a {lumogis_id: $src, user_id: $uid})-[r:RELATES_TO]->(b {lumogis_id: $dst, user_id: $uid}) "
            "DELETE r",
            {"src": src, "dst": dst, "uid": user_id},
        )
        gs.query(
            "MATCH (n {lumogis_id: $id, user_id: $uid}) DELETE n",
            {"id": src, "uid": user_id},
        )
        gs.query(
            "MATCH (n {lumogis_id: $id, user_id: $uid}) DELETE n",
            {"id": dst, "uid": user_id},
        )
    except Exception:
        pass
    try:
        ms.execute("DELETE FROM entity_edges WHERE id = %s", (edge_id,))
    except Exception:
        pass


def test_live_reconcile_attaches_missed_typed_edge_without_touching_relates_to(monkeypatch):
    ms, gs = _live_stores_or_skip(monkeypatch)
    monkeypatch.setitem(config._instances, "metadata_store", ms)

    user_id = f"itest-{uuid.uuid4().hex[:10]}"
    src = f"src-{uuid.uuid4().hex[:8]}"
    dst = f"dst-{uuid.uuid4().hex[:8]}"
    edge_id = uuid.uuid4().hex

    try:
        # Race snapshot: edge durable in Postgres, nodes not yet in FalkorDB.
        ms.execute(
            """
            INSERT INTO entity_edges (
                id, user_id, bank, src_entity_id, dst_entity_id, relation_type
            ) VALUES (%s, %s, 'personal', %s, %s, 'IMPLEMENTS')
            """,
            (edge_id, user_id, src, dst),
        )
        row = ms.fetch_one(
            "SELECT graph_projected_at FROM entity_edges WHERE id = %s",
            (edge_id,),
        )
        assert row is not None and row["graph_projected_at"] is None

        # Nodes arrive asynchronously after the live projection no-op.
        gs.query(
            "MERGE (a {lumogis_id: $src, user_id: $uid}) "
            "MERGE (b {lumogis_id: $dst, user_id: $uid})",
            {"src": src, "dst": dst, "uid": user_id},
        )
        gs.query(
            "MATCH (a {lumogis_id: $src, user_id: $uid}), "
            "(b {lumogis_id: $dst, user_id: $uid}) "
            "MERGE (a)-[r:RELATES_TO]->(b) SET r.co_occurrence_count = 3",
            {"src": src, "dst": dst, "uid": user_id},
        )

        result = reconcile_entity_edges()

        assert result["projected_ok"] >= 1
        attached = gs.query(
            "MATCH (a {lumogis_id: $src, user_id: $uid})-[r:IMPLEMENTS]->(b {lumogis_id: $dst, user_id: $uid}) "
            "RETURN count(r) AS c",
            {"src": src, "dst": dst, "uid": user_id},
        )
        assert attached and int(attached[0]["c"]) == 1

        stamped = ms.fetch_one(
            "SELECT graph_projected_at FROM entity_edges WHERE id = %s",
            (edge_id,),
        )
        assert stamped is not None and stamped["graph_projected_at"] is not None

        cooc = gs.query(
            "MATCH (a {lumogis_id: $src, user_id: $uid})-[r:RELATES_TO]->(b {lumogis_id: $dst, user_id: $uid}) "
            "RETURN r.co_occurrence_count AS n",
            {"src": src, "dst": dst, "uid": user_id},
        )
        assert cooc and int(cooc[0]["n"]) == 3
    finally:
        _cleanup(ms, gs, edge_id, user_id, src, dst)
