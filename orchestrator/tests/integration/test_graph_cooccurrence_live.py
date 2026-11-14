# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live proof of RELATES_TO co_occurrence_count semantics (LUM-952).

The writer computes the count as the number of distinct evidence items two
entities share in ``entity_relations`` and writes it monotonically; the recount
is the only path that lowers it. A mocked metadata store can only check SQL
strings, so this module runs the real aggregate against Postgres and the real
Cypher against FalkorDB, for one throwaway user.

It calls the writer and the recount directly and never runs the whole-database
``reconcile_entities``: that would project every other test's stale rows in a
shared database. The once-per-entity reconcile wiring is pinned by a unit test
(``services/lumogis-graph/tests/test_reconcile_gaps_lum54.py``).

Skips when Postgres or FalkorDB is absent, and fails instead when
``LUMOGIS_REQUIRE_INTEGRATION_DB=1``.
"""

from __future__ import annotations

import importlib.util
import os
import uuid

import pytest
from tests.integration._pgguard import require_postgres

if importlib.util.find_spec("plugins.graph") is None:
    pytest.skip(
        "plugins.graph not in tree (public AGPL export — premium graph plugin omitted)",
        allow_module_level=True,
    )

from plugins.graph.reconcile import recount_cooccurrence
from plugins.graph.writer import _update_cooccurrence_edges

import config


@pytest.fixture
def stores(monkeypatch):
    if not os.environ.get("FALKORDB_URL"):
        require_postgres(RuntimeError("FALKORDB_URL is not set"), what="FalkorDB")
    monkeypatch.setenv("GRAPH_BACKEND", "falkordb")
    monkeypatch.delenv("LUMOGIS_FF_TEMPORAL_KG", raising=False)
    key = config._graph_store_cache_key("personal")
    saved = config._instances.pop(key, None)
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
    except Exception as exc:  # noqa: BLE001 — skip locally, fail when required
        config._instances.pop(key, None)
        if saved is not None:
            config._instances[key] = saved
        require_postgres(exc, what="Postgres/FalkorDB")
    monkeypatch.setitem(config._instances, "metadata_store", ms)
    monkeypatch.setattr(config, "get_graph_max_cooccurrence_pairs", lambda: 100)
    uid = f"itest-cooc-{uuid.uuid4().hex[:8]}"
    yield ms, gs, uid
    gs.query("MATCH (n {user_id: $uid}) DETACH DELETE n", {"uid": uid})
    ms.execute("DELETE FROM entities WHERE user_id = %s", (uid,))
    config._instances.pop(key, None)
    if saved is not None:
        config._instances[key] = saved


def _entity(ms, gs, uid, *, staged=False) -> str:
    eid = str(uuid.uuid4())
    ms.execute(
        "INSERT INTO entities (entity_id, name, entity_type, user_id, is_staged) "
        "VALUES (%s, %s, 'PERSON', %s, %s)",
        (eid, f"n-{eid[:6]}", uid, staged),
    )
    gs.query("MERGE (:Person {lumogis_id: $lid, user_id: $uid})", {"lid": eid, "uid": uid})
    return eid


def _mention(ms, uid, eid, evidence, relation="MENTIONED_IN_DOCUMENT") -> None:
    ms.execute(
        "INSERT INTO entity_relations (source_id, relation_type, evidence_type, evidence_id, "
        "user_id) VALUES (%s, %s, 'DOCUMENT', %s, %s)",
        (eid, relation, evidence, uid),
    )


def _count(gs, uid, a, b):
    lo, hi = sorted((a, b))
    rows = gs.query(
        "MATCH (x {lumogis_id: $lo, user_id: $uid})-[r:RELATES_TO]->"
        "(y {lumogis_id: $hi, user_id: $uid}) RETURN r.co_occurrence_count AS c",
        {"lo": lo, "hi": hi, "uid": uid},
    )
    return None if not rows else rows[0]["c"]


def test_count_is_distinct_shared_evidence(stores):
    ms, gs, uid = stores
    a, b, c = (_entity(ms, gs, uid) for _ in range(3))
    for ev in ("ev1", "ev2"):
        _mention(ms, uid, a, ev)
        _mention(ms, uid, b, ev)
    _mention(ms, uid, a, "ev1", relation="MENTIONED_IN_SESSION")  # same evidence, twice
    _mention(ms, uid, c, "ev1")

    _update_cooccurrence_edges(gs, a, uid, "ev1")

    assert _count(gs, uid, a, b) == 2  # ev1 and ev2, the duplicate row counted once
    assert _count(gs, uid, a, c) == 1


def test_staged_sibling_gets_no_edge(stores):
    ms, gs, uid = stores
    a = _entity(ms, gs, uid)
    d = _entity(ms, gs, uid, staged=True)
    _mention(ms, uid, a, "ev1")
    _mention(ms, uid, d, "ev1")

    _update_cooccurrence_edges(gs, a, uid, "ev1")

    assert _count(gs, uid, a, d) is None


def test_replays_and_both_orders_leave_count_unchanged(stores):
    ms, gs, uid = stores
    a, b = _entity(ms, gs, uid), _entity(ms, gs, uid)
    for ev in ("ev1", "ev2"):
        _mention(ms, uid, a, ev)
        _mention(ms, uid, b, ev)

    sequence = [(a, "ev1"), (b, "ev2"), (b, "ev1"), (a, "ev2"), (a, None), (b, None)]
    for _ in range(2):
        for eid, ev in sequence:
            _update_cooccurrence_edges(gs, eid, uid, ev)
            assert _count(gs, uid, a, b) == 2


def test_stale_lower_snapshot_cannot_lower_count(stores):
    ms, gs, uid = stores
    a, b = _entity(ms, gs, uid), _entity(ms, gs, uid)
    _mention(ms, uid, a, "ev1")
    _mention(ms, uid, b, "ev1")
    _update_cooccurrence_edges(gs, a, uid, "ev1")
    lo, hi = sorted((a, b))
    gs.query(
        "MATCH (x {lumogis_id: $lo, user_id: $uid})-[r:RELATES_TO]->(y {lumogis_id: $hi}) "
        "SET r.co_occurrence_count = 3",
        {"lo": lo, "hi": hi, "uid": uid},
    )

    _update_cooccurrence_edges(gs, b, uid, "ev1")  # its snapshot says 1

    assert _count(gs, uid, a, b) == 3


def test_recount_lowers_inflated_edge_and_skips_typed_and_zero(stores):
    ms, gs, uid = stores
    a, b = _entity(ms, gs, uid), _entity(ms, gs, uid)
    g, h = _entity(ms, gs, uid), _entity(ms, gs, uid)
    e, f = _entity(ms, gs, uid), _entity(ms, gs, uid)
    for ev in ("ev1", "ev2"):
        _mention(ms, uid, a, ev)
        _mention(ms, uid, b, ev)
    _update_cooccurrence_edges(gs, a, uid, "ev1")
    for x, y, props in ((a, b, "{co_occurrence_count: 9}"), (g, h, "{co_occurrence_count: 4}")):
        lo, hi = sorted((x, y))
        gs.query(
            f"MATCH (p {{lumogis_id: $lo, user_id: $uid}}), (q {{lumogis_id: $hi, user_id: $uid}}) "
            f"MERGE (p)-[r:RELATES_TO]->(q) SET r += {props}",
            {"lo": lo, "hi": hi, "uid": uid},
        )
    lo, hi = sorted((e, f))
    gs.query(  # a typed edge (LUM-580 style): shares the slot, carries no count
        "MATCH (p {lumogis_id: $lo, user_id: $uid}), (q {lumogis_id: $hi, user_id: $uid}) "
        "MERGE (p)-[r:RELATES_TO]->(q) SET r.relation_type = 'IMPLEMENTS'",
        {"lo": lo, "hi": hi, "uid": uid},
    )

    # LUM-961 will stamp scope='personal' on personal nodes; the recount must
    # treat that exactly like an absent scope.
    gs.query(
        "MATCH (n:Person {lumogis_id: $a, user_id: $uid}) SET n.scope = 'personal'",
        {"a": a, "uid": uid},
    )

    dry = recount_cooccurrence(user_id=uid, dry_run=True)
    assert dry["edges_changed"] == 1 and _count(gs, uid, a, b) == 9

    result = recount_cooccurrence(user_id=uid)

    assert _count(gs, uid, a, b) == 2
    assert _count(gs, uid, g, h) == 4  # no shared evidence: left alone, not zeroed
    assert _count(gs, uid, e, f) is None  # typed edge never stamped
    assert result["edges_changed"] == 1 and result["zero_skipped"] == 1
