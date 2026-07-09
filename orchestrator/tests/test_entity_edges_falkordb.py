# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live FalkorDB round-trip tests for entity_edges projection/purge (LUM-566).

Opt-in integration: proves against a real FalkorDB that, after the node-key
fix, a projected typed edge is created and then fully deleted by the forget
purge, that purge is user-scoped, and that the co-occurrence-preservation guard
keeps the writer's aggregate ``RELATES_TO`` edge intact.

Run against a local stack (e.g. ``make compose-test-kg`` or any FalkorDB):

    RUN_FALKORDB_ENTITY_EDGES=1 FALKORDB_URL=redis://localhost:6379 \
      pytest -m integration tests/test_entity_edges_falkordb.py -v
"""

from __future__ import annotations

import os
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.integration

falkordb = pytest.importorskip("falkordb")


@pytest.fixture
def falkordb_url():
    if not os.environ.get("RUN_FALKORDB_ENTITY_EDGES"):
        pytest.skip(
            "Set RUN_FALKORDB_ENTITY_EDGES=1 for live FalkorDB entity_edges tests"
        )
    url = os.environ.get("FALKORDB_URL")
    if not url:
        pytest.skip("FALKORDB_URL not set")
    return url


def _graph_for(bank: str):
    import config

    for key in list(config._instances.keys()):
        if key.startswith("graph_store:"):
            config._instances.pop(key, None)
    gs = config.get_graph_store(bank)
    assert gs is not None
    return gs


def _seed_nodes(gs, src: str, dst: str, uid: str) -> None:
    """Create the two entity nodes exactly as the KG writer does."""
    gs.query(
        "MERGE (a {lumogis_id: $src, user_id: $uid}) "
        "MERGE (b {lumogis_id: $dst, user_id: $uid})",
        {"src": src, "dst": dst, "uid": uid},
    )


def _rel_count(gs, src: str, dst: str, uid: str) -> int:
    rows = gs.query(
        "MATCH (a {lumogis_id: $src})-[r:RELATES_TO]->(b {lumogis_id: $dst}) "
        "WHERE r.user_id = $uid RETURN count(r) AS c",
        {"src": src, "dst": dst, "uid": uid},
    )
    if not rows:
        return 0
    row = rows[0]
    return int(row.get("c", row.get("_col0", 0)))


def _fake_ms():
    ms = Mock()
    ms.fetch_one.return_value = {"id": "edge-566"}
    return ms


def _configure_backend(monkeypatch, falkordb_url):
    monkeypatch.setenv("GRAPH_BACKEND", "falkordb")
    monkeypatch.setenv("FALKORDB_URL", falkordb_url)


def test_store_then_forget_purges_projected_edge(monkeypatch, falkordb_url):
    from services import entity_edges

    _configure_backend(monkeypatch, falkordb_url)
    bank = "personal"
    gs = _graph_for(bank)

    src, dst, uid = "ee566-src", "ee566-dst", "ee566-user"
    _seed_nodes(gs, src, dst, uid)

    # Seeding nodes first is deliberate: it factors out the async ENTITY_CREATED
    # node-projection race so this test proves the projection/purge Cypher is
    # correct when nodes exist (the race is a separate, tracked follow-up).
    entity_edges.store_edge(
        user_id=uid, bank=bank, src_entity_id=src, dst_entity_id=dst,
        relation_type="RELATES_TO", ms=_fake_ms(),
    )
    assert _rel_count(gs, src, dst, uid) == 1

    active = [{
        "bank": bank, "src_entity_id": src, "dst_entity_id": dst,
        "relation_type": "RELATES_TO",
    }]
    entity_edges.purge_graph_projections_for_edges(active, user_id=uid)
    assert _rel_count(gs, src, dst, uid) == 0


def test_forget_purge_is_user_scoped(monkeypatch, falkordb_url):
    from services import entity_edges

    _configure_backend(monkeypatch, falkordb_url)
    bank = "personal"
    gs = _graph_for(bank)

    src, dst = "ee566-scope-src", "ee566-scope-dst"
    owner, other = "ee566-owner", "ee566-other"
    _seed_nodes(gs, src, dst, owner)
    entity_edges.store_edge(
        user_id=owner, bank=bank, src_entity_id=src, dst_entity_id=dst,
        relation_type="RELATES_TO", ms=_fake_ms(),
    )
    assert _rel_count(gs, src, dst, owner) == 1

    # A different user's forget must not touch the owner's edge.
    entity_edges.purge_graph_projections_for_edges(
        [{"bank": bank, "src_entity_id": src, "dst_entity_id": dst,
          "relation_type": "RELATES_TO"}],
        user_id=other,
    )
    assert _rel_count(gs, src, dst, owner) == 1


def test_forget_purge_preserves_cooccurrence_edge(monkeypatch, falkordb_url):
    from services import entity_edges

    _configure_backend(monkeypatch, falkordb_url)
    bank = "personal"
    gs = _graph_for(bank)

    # Canonical-direction co-occurrence edge exactly as the writer maintains it.
    lo, hi, uid = "ee566-aaa", "ee566-zzz", "ee566-couser"  # lo < hi
    assert lo < hi
    _seed_nodes(gs, lo, hi, uid)
    gs.query(
        "MATCH (a {lumogis_id: $lo, user_id: $uid}) "
        "MATCH (b {lumogis_id: $hi, user_id: $uid}) "
        "MERGE (a)-[r:RELATES_TO]->(b) "
        "SET r.co_occurrence_count = 2, r.last_seen_at = '2026-07-05T00:00:00Z', "
        "    r.user_id = $uid",
        {"lo": lo, "hi": hi, "uid": uid},
    )
    assert _rel_count(gs, lo, hi, uid) == 1

    # Forgetting a typed RELATES_TO on the SAME (canonical) direction must not
    # delete the aggregate co-occurrence edge.
    entity_edges.purge_graph_projections_for_edges(
        [{"bank": bank, "src_entity_id": lo, "dst_entity_id": hi,
          "relation_type": "RELATES_TO"}],
        user_id=uid,
    )
    rows = gs.query(
        "MATCH (a {lumogis_id: $lo})-[r:RELATES_TO]->(b {lumogis_id: $hi}) "
        "WHERE r.user_id = $uid RETURN r.co_occurrence_count AS n",
        {"lo": lo, "hi": hi, "uid": uid},
    )
    assert rows, "co-occurrence edge was deleted by purge (guard failed)"
    n = rows[0].get("n", rows[0].get("_col0"))
    assert int(n) == 2  # aggregate intact

    # The reverse-direction typed relation is a different edge; the canonical
    # co-occurrence edge must remain untouched by it too.
    entity_edges.purge_graph_projections_for_edges(
        [{"bank": bank, "src_entity_id": hi, "dst_entity_id": lo,
          "relation_type": "RELATES_TO"}],
        user_id=uid,
    )
    assert _rel_count(gs, lo, hi, uid) == 1
