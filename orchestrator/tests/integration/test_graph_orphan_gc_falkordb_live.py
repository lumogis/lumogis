# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live FalkorDB proof for ``garbage_collect_orphan_nodes`` (LUM-54).

The GC pass is the one destructive step of the daily reconcile: it
``DETACH DELETE``s every entity node (``:Entity`` and the ENTITY_TYPE_MAP labels,
LUM-952) whose ``(user_id, lumogis_id)`` has no ``entities`` row. The unit
tests check the bookkeeping against fakes; what only a real graph can show is
that the Cypher deletes exactly the orphan node
of the right user and detaches its edges.

The pass scans the whole graph. To keep this test from deleting anything it
did not create, the graph store is wrapped so the scan returns only this
test's users and the batched delete only carries their rows; the delete Cypher
still runs unchanged against FalkorDB. The wrapper FAILS CLOSED: any other query
raises, so a changed query shape can never reach a real node on a shared graph. The
live entity set comes from a fake metadata store (the pass reads it with one
plain ``SELECT``).

Skips when FalkorDB is absent, and fails instead when
``LUMOGIS_REQUIRE_INTEGRATION_DB=1`` (tier 2 of ``scripts/test-env.sh``).
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

from plugins.graph.reconcile import garbage_collect_orphan_nodes

import config


class _LiveEntities:
    """The pass's only Postgres read: ``SELECT entity_id, user_id FROM entities``."""

    def __init__(self, rows):
        self.rows = rows

    def fetch_all(self, sql, params=None):
        assert "FROM entities" in sql
        return list(self.rows)


_SCAN = "RETURN id(e) AS nid, e.user_id AS uid"
_DELETE = "UNWIND $rows AS r"


class _ScopedGraph:
    """Real FalkorDB store narrowed to this test's users — fails closed."""

    def __init__(self, gs, users):
        self._gs = gs
        self._users = set(users)

    def query(self, cypher, params=None):
        params = dict(params or {})
        if _SCAN in cypher:
            rows = self._gs.query(cypher, params)
            return [r for r in rows if r.get("uid") in self._users]
        if cypher.startswith(_DELETE) and "DETACH DELETE n" in cypher:
            params["rows"] = [r for r in params.get("rows", []) if r.get("uid") in self._users]
            return self._gs.query(cypher, params)
        raise AssertionError(f"unscoped query reached the live graph wrapper: {cypher[:80]!r}")


@pytest.fixture
def live_graph(monkeypatch):
    # No default URL: an unset FALKORDB_URL means "no backend configured", so
    # the hermeticity lane (check-integration-hermeticity) sees a skip.
    if not os.environ.get("FALKORDB_URL"):
        require_postgres(RuntimeError("FALKORDB_URL is not set"), what="FalkorDB")
    monkeypatch.setenv("GRAPH_BACKEND", "falkordb")
    key = config._graph_store_cache_key("personal")
    saved = config._instances.pop(key, None)
    try:
        gs = config.get_graph_store("personal")
        if gs is None or not gs.ping():
            raise RuntimeError("FalkorDB graph store unavailable")
    except Exception as exc:  # noqa: BLE001 — skip locally, fail when required
        config._instances.pop(key, None)
        if saved is not None:
            config._instances[key] = saved
        require_postgres(exc, what="FalkorDB")
    yield gs
    config._instances.pop(key, None)
    if saved is not None:
        config._instances[key] = saved


def _count(gs, uid, lid, label="Entity"):
    rows = gs.query(
        f"MATCH (n:{label}) WHERE n.user_id = $uid AND n.lumogis_id = $lid RETURN count(n) AS c",
        {"uid": uid, "lid": lid},
    )
    return int(rows[0]["c"]) if rows else 0


# An unrelated live entity: a non-empty live set, so the GC's "entities read
# returned nothing" refusal does not apply where a test means "all orphaned".
_KEEP = [{"entity_id": "itest-keeper", "user_id": "itest-keeper"}]


def _run_gc(monkeypatch, gs, users, live, **kwargs):
    monkeypatch.setitem(config._instances, "metadata_store", _LiveEntities(live))
    monkeypatch.setitem(
        config._instances, config._graph_store_cache_key("personal"), _ScopedGraph(gs, users)
    )
    return garbage_collect_orphan_nodes(**kwargs)


def test_live_gc_deletes_only_the_orphan_of_the_right_user(monkeypatch, live_graph):
    gs = live_graph
    alice, bob = f"itest-a-{uuid.uuid4().hex[:8]}", f"itest-b-{uuid.uuid4().hex[:8]}"
    shared_id, doc_id = f"ent-{uuid.uuid4().hex[:8]}", f"doc-{uuid.uuid4().hex[:8]}"
    try:
        # Same lumogis_id for both users; only alice still has an entities row.
        for uid in (alice, bob):
            gs.query(
                "MERGE (e:Entity {lumogis_id: $lid, user_id: $uid}) "
                "MERGE (d:Document {lumogis_id: $doc, user_id: $uid}) "
                "MERGE (d)-[:MENTIONS]->(e)",
                {"lid": shared_id, "uid": uid, "doc": doc_id},
            )

        result = _run_gc(
            monkeypatch, gs, [alice, bob], live=[{"entity_id": shared_id, "user_id": alice}]
        )

        assert result["orphans_found"] == 1 and result["deleted_ok"] == 1
        assert _count(gs, alice, shared_id) == 1
        assert _count(gs, bob, shared_id) == 0
        # DETACH removed bob's edge; the evidence node itself is not an entity and stays.
        assert _count(gs, bob, doc_id, label="Document") == 1
        edges = gs.query(
            "MATCH (d:Document {lumogis_id: $doc, user_id: $uid})-[r:MENTIONS]->() "
            "RETURN count(r) AS c",
            {"doc": doc_id, "uid": bob},
        )
        assert int(edges[0]["c"]) == 0

        again = _run_gc(
            monkeypatch, gs, [alice, bob], live=[{"entity_id": shared_id, "user_id": alice}]
        )
        assert again["orphans_found"] == 0  # a re-run has nothing left to delete
    finally:
        for uid in (alice, bob):
            gs.query("MATCH (n {user_id: $uid}) DETACH DELETE n", {"uid": uid})


def test_live_gc_collects_a_personal_merge_loser(monkeypatch, live_graph):
    gs = live_graph
    uid, loser = f"itest-p-{uuid.uuid4().hex[:8]}", f"ent-{uuid.uuid4().hex[:8]}"
    try:
        gs.query("MERGE (:Person {lumogis_id: $lid, user_id: $uid})", {"lid": loser, "uid": uid})

        result = _run_gc(monkeypatch, gs, [uid], live=_KEEP)

        assert result["orphans_by_label"] == {"Person": 1}
        assert _count(gs, uid, loser, label="Person") == 0
    finally:
        gs.query("MATCH (n {user_id: $uid}) DETACH DELETE n", {"uid": uid})


def test_live_gc_leaves_evidence_and_code_nodes(monkeypatch, live_graph):
    """Evidence and code-structure nodes carry lumogis_id + user_id and no entities row."""
    gs = live_graph
    uid = f"itest-ev-{uuid.uuid4().hex[:8]}"
    labels = ("Document", "Session", "Note", "AudioMemo", "Component", "Library")
    try:
        for label in labels:
            gs.query(
                f"MERGE (:{label} {{lumogis_id: $lid, user_id: $uid}})",
                {"lid": f"{label}-1", "uid": uid},
            )

        result = _run_gc(monkeypatch, gs, [uid], live=_KEEP)

        assert result["scanned"] == 0 and result["orphans_found"] == 0
        for label in labels:
            assert _count(gs, uid, f"{label}-1", label=label) == 1
    finally:
        gs.query("MATCH (n {user_id: $uid}) DETACH DELETE n", {"uid": uid})


def test_live_gc_delete_keeps_same_key_non_entity_node(monkeypatch, live_graph):
    """The delete is label-guarded: a Document sharing the orphan's key survives."""
    gs = live_graph
    uid, lid = f"itest-k-{uuid.uuid4().hex[:8]}", f"ent-{uuid.uuid4().hex[:8]}"
    try:
        gs.query(
            "MERGE (:Person {lumogis_id: $lid, user_id: $uid}) "
            "MERGE (:Document {lumogis_id: $lid, user_id: $uid})",
            {"lid": lid, "uid": uid},
        )

        _run_gc(monkeypatch, gs, [uid], live=_KEEP)

        assert _count(gs, uid, lid, label="Person") == 0
        assert _count(gs, uid, lid, label="Document") == 1
    finally:
        gs.query("MATCH (n {user_id: $uid}) DETACH DELETE n", {"uid": uid})


def test_live_gc_refuses_when_entities_read_is_empty(monkeypatch, live_graph):
    gs = live_graph
    uid = f"itest-e-{uuid.uuid4().hex[:8]}"
    try:
        gs.query(
            "MERGE (:Entity {lumogis_id: 'sh1', user_id: $uid, scope: 'shared'}) "
            "MERGE (:Person {lumogis_id: 'p1', user_id: $uid})",
            {"uid": uid},
        )

        result = _run_gc(monkeypatch, gs, [uid], live=[])

        assert result["refused"] is True and result["deleted_ok"] == 0
        assert _count(gs, uid, "sh1") == 1 and _count(gs, uid, "p1", label="Person") == 1
    finally:
        gs.query("MATCH (n {user_id: $uid}) DETACH DELETE n", {"uid": uid})


def test_live_gc_dry_run_deletes_nothing(monkeypatch, live_graph):
    gs = live_graph
    uid = f"itest-d-{uuid.uuid4().hex[:8]}"
    try:
        gs.query("MERGE (:Concept {lumogis_id: 'c1', user_id: $uid})", {"uid": uid})

        result = _run_gc(monkeypatch, gs, [uid], live=_KEEP, dry_run=True)

        assert result["dry_run"] is True and result["orphans_found"] == 1
        assert _count(gs, uid, "c1", label="Concept") == 1
    finally:
        gs.query("MATCH (n {user_id: $uid}) DETACH DELETE n", {"uid": uid})


def test_live_gc_guard_refuses_mass_delete(monkeypatch, live_graph):
    gs = live_graph
    uid = f"itest-g-{uuid.uuid4().hex[:8]}"
    try:
        gs.query(
            "UNWIND range(1, 40) AS i "
            "CREATE (:Person {lumogis_id: 'p' + toString(i), user_id: $uid})",
            {"uid": uid},
        )
        live = [{"entity_id": f"p{i}", "user_id": uid} for i in range(1, 11)]

        result = _run_gc(monkeypatch, gs, [uid], live=live)  # 30 of 40 orphaned

        assert result["refused"] is True and result["deleted_ok"] == 0
        rows = gs.query("MATCH (n:Person {user_id: $uid}) RETURN count(n) AS c", {"uid": uid})
        assert int(rows[0]["c"]) == 40
    finally:
        gs.query("MATCH (n {user_id: $uid}) DETACH DELETE n", {"uid": uid})


def test_live_gc_guard_never_blocks_shared_teardown(monkeypatch, live_graph):
    """Unshare / purge leave shared :Entity nodes to this pass; the guard must not hold them."""
    gs = live_graph
    uid = f"itest-s-{uuid.uuid4().hex[:8]}"
    try:
        gs.query(
            "UNWIND range(1, 30) AS i "
            "CREATE (:Entity {lumogis_id: 'sh' + toString(i), user_id: $uid, scope: 'shared'}), "
            "(:Person {lumogis_id: 'p' + toString(i), user_id: $uid})",
            {"uid": uid},
        )

        result = _run_gc(monkeypatch, gs, [uid], live=_KEEP)

        assert result["refused"] is True
        assert result["deleted_ok"] == 30
        shared = gs.query("MATCH (n:Entity {user_id: $uid}) RETURN count(n) AS c", {"uid": uid})
        personal = gs.query("MATCH (n:Person {user_id: $uid}) RETURN count(n) AS c", {"uid": uid})
        assert int(shared[0]["c"]) == 0 and int(personal[0]["c"]) == 30
    finally:
        gs.query("MATCH (n {user_id: $uid}) DETACH DELETE n", {"uid": uid})
