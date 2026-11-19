# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live proof that a kind correction relabels the graph node in place (LUM-967).

``create_node`` MERGEs on label + ``lumogis_id`` + ``user_id``. Projecting a
corrected entry under its new label without first moving the old node would
create a **second** node that the orphan GC cannot see and that the converge can
never merge. These tests run the real writer against a real FalkorDB and assert
what only a real graph shows: one node, the same ``id(n)``, the new label only,
the edges kept.

* the hermetic contract (statement text, order, lock, stamp) is
  ``services/lumogis-graph/tests/test_writer_entity_relabel_lum967.py``;
* ``test_negative_control_without_converge_duplicates`` proves the live tests can
  fail (the converge no-op'd gives two nodes);
* ``test_patch_then_reconcile_relabels_live`` runs the real chain end to end:
  ``change_entity_kind`` (Postgres) → stale row → ``reconcile_entities`` → graph.

Each test uses its own graph (``lum967_relabel_<hex>``), deleted afterwards.
Skips when FalkorDB is absent; fails instead under
``LUMOGIS_REQUIRE_INTEGRATION_DB=1`` (CI ``lint-and-test``, ``make test-env-integration``).
"""

from __future__ import annotations

import importlib.util
import os
import threading
import uuid

import pytest
from auth import UserContext
from tests.integration._kg_live import live_pg  # noqa: F401 — fixture
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

USER = "alice"
OTHER = "bob"


class _FakeMS:
    """The writer's Postgres reads, canned per test: the entity row and co-occurrence siblings."""

    def __init__(self, row=None, siblings=()):
        self.row = row
        self.siblings = list(siblings)
        self.executed: list[tuple[str, tuple | None]] = []

    def fetch_one(self, sql, params=None):
        return dict(self.row) if self.row is not None else None

    def fetch_all(self, sql, params=None):
        if "sibling_id" in sql:
            return list(self.siblings)
        return []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


@pytest.fixture
def falkordb_graph():
    from adapters.falkordb_store import FalkorDBStore

    url = os.environ.get("FALKORDB_URL", "")
    name = f"lum967_relabel_{uuid.uuid4().hex[:8]}"
    try:
        if not url:
            raise RuntimeError("FALKORDB_URL not set")
        gs = FalkorDBStore(url=url, graph_name=name)
        if not gs.ping():
            raise RuntimeError("FalkorDB ping failed")
    except Exception as exc:  # pragma: no cover - env-dependent
        require_postgres(exc, what="FalkorDB")
    try:
        yield gs
    finally:
        try:
            gs._graph().delete()
        except Exception:
            pass


@pytest.fixture
def world(falkordb_graph, monkeypatch):
    ms = _FakeMS()
    monkeypatch.setattr(config, "get_metadata_store", lambda: ms)
    monkeypatch.setattr(config, "get_graph_store", lambda *a, **k: falkordb_graph)

    class W:
        pass

    w = W()
    w.gs, w.ms = falkordb_graph, ms
    return w


def _ids():
    return str(uuid.uuid4()), str(uuid.uuid4())


def _project(w, eid, kind, *, evidence="/d/doc.md", user=USER, ms=None, **kw):
    gw.project_entity(
        w.gs,
        entity_id=eid,
        entity_type=kind,
        name=f"Entry {eid[:4]}",
        evidence_id=evidence,
        evidence_type="DOCUMENT",
        user_id=user,
        ms=ms or w.ms,
        **kw,
    )


def _nodes(gs, eid):
    return gs.query(
        "MATCH (n) WHERE n.lumogis_id = $lid RETURN id(n) AS i, labels(n) AS l, n.user_id AS u",
        {"lid": eid},
    )


def _row(kind, projected=None):
    return {
        "memory_type": None,
        "last_verified_at": None,
        "entity_type": kind,
        "graph_projected_at": projected,
    }


def _edge_count(gs, eid, rel):
    return gs.query(
        f"MATCH (a)-[r:{rel}]-(n) WHERE n.lumogis_id = $lid RETURN count(r) AS c",  # noqa: S608
        {"lid": eid},
    )[0]["c"]


def _seed_two_entities(w, e1, e2):
    """E1 and E2 PERSON nodes sharing evidence ``/d/doc.md`` (MENTIONS + RELATES_TO)."""
    w.ms.siblings = [{"sibling_id": e2, "shared": 1, "last_shared_at": None}]
    w.ms.row = _row("PERSON", projected=None)
    _project(w, e2, "PERSON")
    _project(w, e1, "PERSON")


def test_relabel_in_place_keeps_node_and_edges(world):
    e1, e2 = _ids()
    _seed_two_entities(world, e1, e2)
    (before,) = _nodes(world.gs, e1)
    assert before["l"] == ["Person"]
    mentions = _edge_count(world.gs, e1, "MENTIONS")
    relates = _edge_count(world.gs, e1, "RELATES_TO")
    assert mentions == 1 and relates == 1

    world.ms.row = _row("ORG", projected=None)  # the corrected, stale row
    _project(world, e1, "ORG")

    (after,) = _nodes(world.gs, e1)
    assert after["l"] == ["Organisation"]
    assert after["i"] == before["i"]  # the same node, not a recreated one
    assert _edge_count(world.gs, e1, "MENTIONS") == mentions
    assert _edge_count(world.gs, e1, "RELATES_TO") == relates
    assert (
        world.gs.query("MATCH (n:Person {lumogis_id: $lid}) RETURN count(n) AS c", {"lid": e1})[0][
            "c"
        ]
        == 0
    )
    # the neighbour is untouched
    assert [n["l"] for n in _nodes(world.gs, e2)] == [["Person"]]


def test_relabel_idempotent(world):
    e1, e2 = _ids()
    _seed_two_entities(world, e1, e2)
    world.ms.row = _row("ORG", projected=None)
    _project(world, e1, "ORG")
    (first,) = _nodes(world.gs, e1)
    _project(world, e1, "ORG")  # a second and third projection: converge is a no-op
    world.ms.row = _row("ORG", projected="now")
    _project(world, e1, "ORG")
    nodes = _nodes(world.gs, e1)
    assert len(nodes) == 1 and nodes[0]["i"] == first["i"] and nodes[0]["l"] == ["Organisation"]


def test_relabel_scoped_to_user(world):
    e1, _ = _ids()
    world.ms.row = _row("PERSON")
    _project(world, e1, "PERSON", user=OTHER)  # another member's node with the same lumogis_id
    world.ms.row = _row("ORG")
    _project(world, e1, "ORG")  # Alice's projection
    rows = {n["u"]: n["l"] for n in _nodes(world.gs, e1)}
    assert rows == {OTHER: ["Person"], USER: ["Organisation"]}
    # relabelling Alice's node must not move Bob's: project Alice again under CONCEPT
    world.ms.row = _row("CONCEPT")
    _project(world, e1, "CONCEPT")
    rows = {n["u"]: n["l"] for n in _nodes(world.gs, e1)}
    assert rows == {OTHER: ["Person"], USER: ["Concept"]}


def test_negative_control_without_converge_duplicates(world, monkeypatch):
    """With the converge a no-op, MERGE on the new label creates a second node —
    so the in-place test above would have caught the failure the converge prevents."""
    e1, e2 = _ids()
    _seed_two_entities(world, e1, e2)
    monkeypatch.setattr(gw, "_converge_entity_label", lambda *a, **k: None)
    world.ms.row = _row("ORG", projected=None)
    _project(world, e1, "ORG")
    assert sorted(tuple(n["l"]) for n in _nodes(world.gs, e1)) == [("Organisation",), ("Person",)]


def test_stale_event_after_correction_keeps_new_label(world):
    e1, e2 = _ids()
    _seed_two_entities(world, e1, e2)
    world.ms.row = _row("ORG", projected=None)
    _project(world, e1, "ORG")
    (first,) = _nodes(world.gs, e1)
    # A late ENTITY_CREATED still carrying the pre-correction kind: the stored row wins.
    _project(world, e1, "PERSON")
    (node,) = _nodes(world.gs, e1)
    assert node["l"] == ["Organisation"] and node["i"] == first["i"]


def test_out_of_order_projectors_never_duplicate(world):
    """The interleaving the per-entity lock exists for, driven deterministically.

    B projects the corrected (ORG, stale) row; then a late projector A whose row
    read returned the pre-commit PERSON kind runs; then B again. After every step
    exactly one node carries ``lumogis_id`` and it keeps its id.
    """
    e1, e2 = _ids()
    _seed_two_entities(world, e1, e2)
    (orig,) = _nodes(world.gs, e1)

    ms_b = _FakeMS(_row("ORG", projected=None))
    ms_a = _FakeMS(_row("PERSON", projected=None))  # read before the PATCH committed

    _project(world, e1, "ORG", ms=ms_b)
    nodes = _nodes(world.gs, e1)
    assert [n["l"] for n in nodes] == [["Organisation"]] and nodes[0]["i"] == orig["i"]

    _project(world, e1, "PERSON", ms=ms_a)  # A converges back to Person, in place
    nodes = _nodes(world.gs, e1)
    assert len(nodes) == 1 and nodes[0]["i"] == orig["i"]
    # A's stamp carries the kind A projected; a real row of kind ORG would not match it.
    ((_, a_params),) = ms_a.executed
    assert a_params[-1] == "PERSON"

    _project(world, e1, "ORG", ms=ms_b)
    nodes = _nodes(world.gs, e1)
    assert len(nodes) == 1 and nodes[0]["i"] == orig["i"] and nodes[0]["l"] == ["Organisation"]
    ((_, b_params),) = ms_b.executed[:1]
    assert b_params[-1] == "ORG"


def test_concurrent_projectors_one_node(world):
    """Two real threads projecting one entry under different stored kinds: the lock
    orders read → converge → MERGE, so there is never a second node."""
    e1, e2 = _ids()
    _seed_two_entities(world, e1, e2)
    errors: list[BaseException] = []

    def go(kind):
        try:
            for _ in range(5):
                _project(world, e1, kind, ms=_FakeMS(_row(kind, projected=None)))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(k,)) for k in ("ORG", "PERSON", "ORG", "PROJECT")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors, errors
    assert len(_nodes(world.gs, e1)) == 1


def test_patch_then_reconcile_relabels_live(live_pg, world, monkeypatch):  # noqa: F811
    """PATCH service → stale row → reconcile → graph, through the real chain."""
    ms = live_pg
    sfx = uuid.uuid4().hex[:8]
    alice = f"lum967-graph-{sfx}"
    monkeypatch.setitem(config._instances, "embedder", MockEmbedder())
    monkeypatch.setitem(config._instances, "vector_store", MockVectorStore())
    row = ms.fetch_one(
        "INSERT INTO entities (name, entity_type, user_id, scope) "
        "VALUES (%s, 'PERSON', %s, 'personal') RETURNING entity_id",
        (f"Meier Pharmacy {sfx}", alice),
    )
    eid = str(row["entity_id"])
    ms.execute(
        "INSERT INTO entity_relations (source_id, relation_type, evidence_type, evidence_id, "
        "user_id) VALUES (%s, 'MENTIONED_IN_DOCUMENT', 'DOCUMENT', %s, %s)",
        (eid, f"/data/{sfx}/a.md", alice),
    )
    # The shared graph store and metadata store are the live ones; other stale rows in
    # the shared database may also be projected into the per-test graph — assert on E1 only.
    monkeypatch.setattr(config, "get_metadata_store", lambda: ms)
    try:
        graph_reconcile.reconcile_entities()
        (first,) = _nodes(world.gs, eid)
        assert first["l"] == ["Person"]
        stamped = ms.fetch_one(
            "SELECT graph_projected_at FROM entities WHERE entity_id = %s", (eid,)
        )
        assert stamped["graph_projected_at"] is not None

        user = UserContext(user_id=alice, allows_shared=True, is_authenticated=True)
        v = ms.fetch_one("SELECT version FROM entities WHERE entity_id = %s", (eid,))["version"]
        entity_edit.change_entity_kind(user, eid, entity_type="ORG", version=v)
        stale = ms.fetch_one("SELECT graph_projected_at FROM entities WHERE entity_id = %s", (eid,))
        assert stale["graph_projected_at"] is None

        graph_reconcile.reconcile_entities()
        (after,) = _nodes(world.gs, eid)
        assert after["l"] == ["Organisation"] and after["i"] == first["i"]
        assert _edge_count(world.gs, eid, "MENTIONS") == 1
        done = ms.fetch_one(
            "SELECT graph_projected_at, entity_type FROM entities WHERE entity_id = %s", (eid,)
        )
        assert done["graph_projected_at"] is not None and done["entity_type"] == "ORG"
    finally:
        for table in ("entity_relations", "entities", "entity_name_rules"):
            ms.execute(f"DELETE FROM {table} WHERE user_id = %s", (alice,))  # noqa: S608
