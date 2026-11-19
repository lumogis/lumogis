# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live proof that the graph follows a delete of an entry (LUM-966).

Real Postgres and real FalkorDB, the writer called directly (``GRAPH_MODE=inprocess``
shape, as the merge/undo live tests do). The hermetic contract is
``services/lumogis-graph/tests/test_writer_entity_delete_lum966.py``.

Each test uses its own graph (``lum966_<hex>``), deleted afterwards. These must
run with FalkorDB up and 0 skipped at verify: ``scripts/test-env.sh run --tier 2``.
"""

# ruff: noqa: F811

from __future__ import annotations

import importlib.util
import os
import uuid

import pytest
from auth import UserContext
from tests.integration._kg_live import _ent
from tests.integration._kg_live import _version
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


@pytest.fixture
def gs():
    from adapters.falkordb_store import FalkorDBStore

    url = os.environ.get("FALKORDB_URL", "")
    name = f"lum966_{uuid.uuid4().hex[:8]}"
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
    monkeypatch.setattr("services.entity_deletions.hooks.fire_background", lambda *a, **k: None)
    config.set_effective_graph_mode_for_process("inprocess")
    world["gs"] = gs
    try:
        yield world
    finally:
        config.set_effective_graph_mode_for_process(None)
        config.clear_graph_mode_env_cache()
        members = (world["alice"], world["bob"], world["nosharing"])
        for table in ("entity_relations", "entity_deletions"):
            ms.execute(f"DELETE FROM {table} WHERE user_id IN (%s, %s, %s)", members)  # noqa: S608


def _user(uid):
    return UserContext(user_id=uid, is_authenticated=True, role="user", allows_shared=True)


def _nodes(gs, lid):
    rows = gs.query("MATCH (n) WHERE n.lumogis_id = $lid RETURN labels(n) AS labels", {"lid": lid})
    return [r["labels"] for r in rows]


def _project(gs, eid, name, uid, evidence="doc-1", kind="PERSON"):
    gw.project_entity(
        gs,
        entity_id=eid,
        entity_type=kind,
        name=name,
        evidence_id=evidence,
        evidence_type="DOCUMENT",
        user_id=uid,
    )


def _shared_node(gs, proj, source, uid, name):
    gs.create_node(
        labels=["Entity"],
        properties={
            "lumogis_id": proj,
            "name": name,
            "entity_type": "PERSON",
            "user_id": uid,
            "scope": "shared",
            "published_from": source,
        },
    )


def _tombstone(ms, eid):
    return ms.fetch_one(
        "SELECT *, projection_ids::text[] AS pids FROM entity_deletions WHERE entity_id = %s",
        (eid,),
    )


def test_F1_the_handler_removes_the_node_its_shared_copy_and_stamps_the_tombstone(env):
    ms, gs, alice = env["ms"], env["gs"], env["alice"]
    eid = _ent(ms, "Total", alice)
    keep = _ent(ms, "Neighbour", alice)
    _project(gs, eid, "Total", alice)
    _project(gs, keep, "Neighbour", alice)  # same evidence: a co-occurrence edge
    proj = str(uuid.uuid4())
    _shared_node(gs, proj, eid, alice, "Total")
    assert _nodes(gs, eid) and _nodes(gs, proj)

    out = entity_edit.delete_entry(_user(alice), eid, version=_version(ms, eid))
    tomb = _tombstone(ms, eid)
    assert tomb["graph_cleared_at"] is None  # the graph is enabled: its arm is the handler's
    gw.on_entity_deleted(entity_id=eid, user_id=alice, projection_ids=[proj])

    assert _nodes(gs, eid) == [] and _nodes(gs, proj) == []
    assert _nodes(gs, keep) == [["Person"]]  # the neighbour stays
    assert _tombstone(ms, eid)["graph_cleared_at"] is not None
    assert out.entity_id == eid
    # Idempotent: a re-fired event is a no-op.
    gw.on_entity_deleted(entity_id=eid, user_id=alice, projection_ids=[proj])


def test_F2_the_handler_never_touches_another_members_node(env):
    ms, gs, alice, bob = env["ms"], env["gs"], env["alice"], env["bob"]
    eid = _ent(ms, "Total", alice)
    _project(gs, eid, "Total", alice)
    gw.on_entity_deleted(entity_id=eid, user_id=bob, projection_ids=[])  # negative control
    assert _nodes(gs, eid) == [["Person"]]


def test_F3_a_late_entity_created_after_a_delete_writes_no_node(env):
    ms, gs, alice = env["ms"], env["gs"], env["alice"]
    eid = _ent(ms, "Total", alice)
    _project(gs, eid, "Total", alice)
    entity_edit.delete_entry(_user(alice), eid, version=_version(ms, eid))
    gw.on_entity_deleted(entity_id=eid, user_id=alice, projection_ids=[])
    gw.on_entity_created(
        entity_id=eid,
        name="Total",
        entity_type="PERSON",
        evidence_id="doc-late",
        evidence_type="DOCUMENT",
        user_id=alice,
    )
    assert _nodes(gs, eid) == []
    # Negative control: with no tombstone the same event for a missing row still writes.
    ghost = str(uuid.uuid4())
    gw.on_entity_created(
        entity_id=ghost,
        name="Ghost",
        entity_type="PERSON",
        evidence_id="doc-late",
        evidence_type="DOCUMENT",
        user_id=alice,
    )
    assert _nodes(gs, ghost) == [["Person"]]


def test_F4_when_the_event_is_lost_the_orphan_gc_still_removes_the_node(env):
    ms, gs, alice = env["ms"], env["gs"], env["alice"]
    eid = _ent(ms, "Total", alice)
    _project(gs, eid, "Total", alice)
    entity_edit.delete_entry(_user(alice), eid, version=_version(ms, eid))
    assert _nodes(gs, eid) == [["Person"]]  # the webhook never arrived
    result = graph_reconcile.garbage_collect_orphan_nodes(force=True)
    assert result["deleted_ok"] >= 1
    assert _nodes(gs, eid) == []
