# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live proof that the graph follows an undo through reconcile (LUM-965, G2).

Real Postgres and real FalkorDB. Undo fires no graph event: it stamps the rows
stale, and ``reconcile_entities`` replays them and then prunes what Postgres no
longer backs (``graph/reconcile.py::_prune_merged_entity``). The hermetic
contract is ``services/lumogis-graph/tests/test_reconcile_prune.py``.

Each test uses its own graph (``lum965_<hex>``), deleted afterwards. These must
run with FalkorDB up and 0 skipped at verify: ``scripts/test-env.sh run --tier 2``.
"""

# ruff: noqa: F811

from __future__ import annotations

import importlib.util
import os
import uuid

import pytest
from tests.integration._kg_live import _ent
from tests.integration._kg_live import live_pg  # noqa: F401 — fixture
from tests.integration._kg_live import world  # noqa: F401 — fixture
from tests.integration._pgguard import require_postgres

if importlib.util.find_spec("plugins.graph") is None:
    pytest.skip(
        "plugins.graph not in tree (Core built without the graph plugin)",
        allow_module_level=True,
    )

import plugins.graph.writer  # noqa: F401 — puts services/lumogis-graph on sys.path
from graph import reconcile as graph_reconcile
from graph import writer as gw
from tests.conftest import MockEmbedder
from tests.conftest import MockVectorStore

import config
from services import entity_edit
from services import entity_merge


@pytest.fixture
def gs():
    from adapters.falkordb_store import FalkorDBStore

    url = os.environ.get("FALKORDB_URL", "")
    name = f"lum965_{uuid.uuid4().hex[:8]}"
    try:
        if not url:
            raise RuntimeError("FALKORDB_URL not set")
        store = FalkorDBStore(url=url, graph_name=name)
        if not store.ping():
            raise RuntimeError("FalkorDB ping failed")
    except Exception as exc:  # pragma: no cover - env-dependent
        require_postgres(exc, what="FalkorDB")
    try:
        yield store
    finally:
        try:
            store._graph().delete()
        except Exception:
            pass


@pytest.fixture
def env(world, gs, monkeypatch):
    ms = world["ms"]
    monkeypatch.setitem(config._instances, "vector_store", MockVectorStore())
    monkeypatch.setitem(config._instances, "embedder", MockEmbedder())
    monkeypatch.setattr(config, "get_graph_store", lambda *a, **k: gs)
    monkeypatch.setattr("services.entity_merge.hooks.fire_background", lambda *a, **k: None)
    monkeypatch.setenv("LUMOGIS_GRAPH_DEFAULT_BANK", "personal")
    world["gs"] = gs
    try:
        yield world
    finally:
        members = (world["alice"], world["bob"], world["nosharing"])
        for table in (
            "entity_merges",
            "entity_relations",
            "entity_edges",
            "known_distinct_entity_pairs",
        ):
            ms.execute(f"DELETE FROM {table} WHERE user_id IN (%s, %s, %s)", members)  # noqa: S608


def _rel(ms, uid, entity, evidence, etype="DOCUMENT", rtype="MENTIONED_IN_DOCUMENT"):
    ms.execute(
        "INSERT INTO entity_relations (source_id, relation_type, evidence_type, evidence_id, "
        "user_id) VALUES (%s,%s,%s,%s,%s)",
        (entity, rtype, etype, evidence, uid),
    )


def _mentions(gs, eid):
    rows = gs.query(
        "MATCH (s)-[r:MENTIONS]->(e) WHERE e.lumogis_id = $eid RETURN r.evidence_id AS ev",
        {"eid": eid},
    )
    return sorted(r["ev"] for r in rows)


def _reconcile():
    return graph_reconcile.reconcile_entities()


def _merge_and_project(ms, gs, w, lo, uid):
    """Project both, merge, run the webhook handler the way the hook would."""
    _reconcile()
    res = entity_merge.merge_entities(w, lo, uid, origin="member")
    gw.on_entity_merged(gs, winner_id=w, loser_id=lo, user_id=uid) if hasattr(
        gw, "on_entity_merged"
    ) else None
    return res


def _undo(user, merge_id):
    return entity_edit.undo_merge(user, merge_id)


def _user(uid):
    from auth import UserContext

    return UserContext(user_id=uid, is_authenticated=True)


def test_F0_prune_cypher_smoke(env):
    """The prune's statements parse and delete exactly the intended edge."""
    ms, gs, uid = env["ms"], env["gs"], env["alice"]
    a, b = _ent(ms, "A", uid), _ent(ms, "B", uid)
    _rel(ms, uid, a, "/d/keep.md")
    _rel(ms, uid, a, "/d/gone.md")
    _reconcile()
    assert _mentions(gs, a) == ["/d/gone.md", "/d/keep.md"]
    graph_reconcile._prune_merged_entity(
        gs, ms, entity_id=a, entity_type="PERSON", user_id=uid, keep_evidence=["/d/keep.md"]
    )
    assert _mentions(gs, a) == ["/d/keep.md"]
    # A typed edge with no backing row goes; one with a row stays.
    gs.query(
        "MATCH (x {lumogis_id: $a}), (y {lumogis_id: $b}) "
        "CREATE (x)-[:WORKS_WITH]->(y), (x)-[:KNOWS]->(y)",
        {"a": a, "b": b},
    )
    ms.execute(
        "INSERT INTO entity_edges (id, user_id, bank, src_entity_id, dst_entity_id, "
        "relation_type) VALUES (%s,%s,'personal',%s,%s,'KNOWS'), "
        "(%s,%s,'personal',%s,%s,'WORKS_WITH')",
        (uuid.uuid4().hex, uid, a, b, uuid.uuid4().hex, uid, b, a),
    )
    graph_reconcile._prune_merged_entity(
        gs, ms, entity_id=a, entity_type="PERSON", user_id=uid, keep_evidence=["/d/keep.md"]
    )
    kinds = sorted(
        r["t"]
        for r in gs.query(
            "MATCH (x {lumogis_id: $a})-[r]-(y) WHERE type(r) <> 'MENTIONS' RETURN type(r) AS t",
            {"a": a},
        )
    )
    assert kinds == ["KNOWS"]


def test_F1_undo_restores_loser_node_and_prunes_winner_mentions(env):
    ms, gs, uid = env["ms"], env["gs"], env["alice"]
    w = _ent(ms, "Dr. Meier", uid)
    lo = _ent(ms, "Dr. A. Meier", uid)
    y = _ent(ms, "Only Loser Knows", uid)
    _rel(ms, uid, w, "/d/w.md")
    _rel(ms, uid, lo, "/d/l.md")
    _rel(ms, uid, lo, "/d/l2.md")
    _rel(ms, uid, y, "/d/l2.md")  # co-occurs only with the loser
    _reconcile()
    res = entity_merge.merge_entities(w, lo, uid, origin="member")
    _reconcile()
    assert _mentions(gs, w) == ["/d/l.md", "/d/l2.md", "/d/w.md"]
    _undo(_user(uid), res.merge_id)
    counter = _reconcile()
    assert counter.get("prune_failed", 0) == 0
    assert _mentions(gs, lo) == ["/d/l.md", "/d/l2.md"]
    assert _mentions(gs, w) == ["/d/w.md"]

    # W has no co-occurrence edge to Y (they share no evidence); L has.
    def co(eid):
        return sorted(
            r["o"]
            for r in gs.query(
                "MATCH (a {lumogis_id: $e})-[r:RELATES_TO]-(o) "
                "WHERE r.co_occurrence_count IS NOT NULL RETURN o.lumogis_id AS o",
                {"e": eid},
            )
        )

    assert y not in co(w)
    assert y in co(lo)


def test_F2_undo_moves_typed_edge_back_in_graph(env):
    ms, gs, uid = env["ms"], env["gs"], env["alice"]
    w, lo, x = _ent(ms, "W", uid), _ent(ms, "L", uid), _ent(ms, "X", uid)
    _rel(ms, uid, w, "/d/w.md")
    _rel(ms, uid, lo, "/d/l.md")
    _rel(ms, uid, x, "/d/x.md")
    ms.execute(
        "INSERT INTO entity_edges (id, user_id, bank, src_entity_id, dst_entity_id, "
        "relation_type) VALUES (%s,%s,'personal',%s,%s,'WORKS_WITH')",
        (uuid.uuid4().hex, uid, lo, x),
    )
    _reconcile()
    graph_reconcile.reconcile_entity_edges()
    res = entity_merge.merge_entities(w, lo, uid, origin="member")
    _reconcile()
    graph_reconcile.reconcile_entity_edges()
    _undo(_user(uid), res.merge_id)
    _reconcile()
    graph_reconcile.reconcile_entity_edges()

    def works_with(src):
        return gs.query(
            "MATCH (a {lumogis_id: $s})-[r:WORKS_WITH]->(b {lumogis_id: $x}) RETURN count(r) AS n",
            {"s": src, "x": x},
        )[0]["n"]

    assert works_with(lo) == 1 and works_with(w) == 0


def test_F3_undo_discussed_in_follows_session(env):
    ms, gs, uid = env["ms"], env["gs"], env["alice"]
    w, lo = _ent(ms, "W", uid), _ent(ms, "L", uid)
    sid = str(uuid.uuid4())
    ms.execute(
        "INSERT INTO sessions (session_id, user_id, entity_ids, summary) VALUES (%s,%s,%s,'s')",
        (sid, uid, [lo]),
    )
    _rel(ms, uid, lo, sid, "SESSION", "MENTIONED_IN_SESSION")
    _rel(ms, uid, w, "/d/w.md")
    _reconcile()
    graph_reconcile.reconcile_sessions()
    res = entity_merge.merge_entities(w, lo, uid, origin="member")
    _reconcile()
    graph_reconcile.reconcile_sessions()
    _undo(_user(uid), res.merge_id)
    _reconcile()
    graph_reconcile.reconcile_sessions()

    def discussed(eid):
        return gs.query(
            "MATCH (e {lumogis_id: $e})-[r:DISCUSSED_IN]->(s:Session {lumogis_id: $s}) "
            "RETURN count(r) AS n",
            {"e": eid, "s": sid},
        )[0]["n"]

    assert discussed(lo) == 1 and discussed(w) == 0


def test_F4_prune_keeps_legacy_session_edges(env):
    ms, gs, uid = env["ms"], env["gs"], env["alice"]
    w, lo = _ent(ms, "W", uid), _ent(ms, "L", uid)
    _rel(ms, uid, w, "/d/w.md")
    sid = str(uuid.uuid4())
    ms.execute(
        "INSERT INTO sessions (session_id, user_id, entity_ids, summary) VALUES (%s,%s,%s,'s')",
        (sid, uid, []),  # legacy: edges are resolved by name, not id
    )
    _reconcile()
    gs.create_node(
        labels=["Session"], properties={"lumogis_id": sid, "user_id": uid, "scope": "personal"}
    )
    gs.query(
        "MATCH (e {lumogis_id: $e}), (s:Session {lumogis_id: $s}) CREATE (e)-[:DISCUSSED_IN]->(s)",
        {"e": w, "s": sid},
    )
    res = entity_merge.merge_entities(w, lo, uid, origin="member")  # W is merge-involved
    _undo(_user(uid), res.merge_id)
    _reconcile()
    n = gs.query(
        "MATCH (e {lumogis_id: $e})-[r:DISCUSSED_IN]->(s {lumogis_id: $s}) RETURN count(r) AS n",
        {"e": w, "s": sid},
    )[0]["n"]
    assert n == 1


def test_negative_control_without_prune_the_survivor_keeps_loser_mentions(env, monkeypatch):
    """Proves F1 can fail: with the prune no-op'd the survivor keeps the loser's MENTIONS."""
    ms, gs, uid = env["ms"], env["gs"], env["alice"]
    w, lo = _ent(ms, "W", uid), _ent(ms, "L", uid)
    _rel(ms, uid, w, "/d/w.md")
    _rel(ms, uid, lo, "/d/l.md")
    _reconcile()
    res = entity_merge.merge_entities(w, lo, uid, origin="member")
    _reconcile()
    _undo(_user(uid), res.merge_id)
    monkeypatch.setattr(graph_reconcile, "_prune_merged_entity", lambda *a, **k: None)
    _reconcile()
    assert "/d/l.md" in _mentions(gs, w)
