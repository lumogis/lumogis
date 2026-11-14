# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live writer → reader round trip for graph node ``scope`` (LUM-961, ADR 015).

Before LUM-961 the personal graph writers never set ``scope`` on a node, so
``visible_cypher_fragment`` hid every personal node from every filtered read:
``/graph/stats`` reported ``node_count`` 0 for a member who had ingested
documents. The hermetic suite (``services/lumogis-graph/tests/
test_writer_node_scope_lum961.py``) proves each writer stamps ``scope``; only a
real FalkorDB shows the stamped node is actually *returned* by the real readers.

Everything is written through the real writer (``plugins.graph.writer``) into a
dedicated per-test graph, and read through the real ``get_stats`` route handler
and ``plugins.graph.query.mention_sources`` — neither re-implements a reader.
Both build their own ``UserContext(user_id=…)`` with ``allows_shared=None``,
which resolves through ``services.users.get_allows_shared`` →
``SELECT allows_shared FROM users``. The fake metadata store below answers that
lookup per member, so the production path is the one under test.

Skips when FalkorDB is absent, and fails instead when
``LUMOGIS_REQUIRE_INTEGRATION_DB=1`` (CI ``lint-and-test``).
"""

from __future__ import annotations

import importlib.util
import os
import sys
import uuid

import pytest
from auth import UserContext
from tests.integration._pgguard import require_postgres
from visibility import visible_cypher_fragment

if importlib.util.find_spec("plugins.graph") is None:
    pytest.skip(
        "plugins.graph not in tree (Core built without the graph plugin)",
        allow_module_level=True,
    )

import plugins.graph.query as graph_query
import plugins.graph.viz_routes as viz_routes
import plugins.graph.writer as graph_writer

import config

_ALLOWS_SHARED = {"alice": True, "bob": True, "dana": False}


class _FakeMS:
    """Metadata store fake: no rows, except the ``allows_shared`` lookup per member."""

    def fetch_one(self, sql, params=None):
        if "SELECT allows_shared FROM users" in sql:
            uid = params[0]
            if uid not in _ALLOWS_SHARED:
                return None
            return {"allows_shared": _ALLOWS_SHARED[uid]}
        return None

    def fetch_all(self, sql, params=None):
        return []

    def execute(self, sql, params=None):
        return None


@pytest.fixture
def falkordb_graph():
    from adapters.falkordb_store import FalkorDBStore

    url = os.environ.get("FALKORDB_URL", "")
    name = f"lum961_scope_{uuid.uuid4().hex[:8]}"
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
    """The per-test graph, wired into config, and a ``stats(uid)`` helper."""
    ms = _FakeMS()
    gs = falkordb_graph
    monkeypatch.setattr(config, "get_metadata_store", lambda: ms)
    monkeypatch.setattr(config, "get_graph_store", lambda *a, **k: gs)
    # ``_update_cooccurrence_edges`` reads the store without an ``ms`` argument.
    holder = {"uid": "alice"}
    route_module = sys.modules[viz_routes.get_stats.__module__]
    monkeypatch.setattr(route_module, "_require_auth", lambda request: holder["uid"])

    class World:
        pass

    w = World()
    w.gs, w.ms = gs, ms

    def stats(uid: str) -> dict:
        holder["uid"] = uid
        return viz_routes.get_stats(None)

    w.stats = stats
    return w


def _member(uid: str) -> UserContext:
    """Always pass ``allows_shared`` explicitly when building a context by hand."""
    return UserContext(
        user_id=uid,
        role="user",
        is_authenticated=True,
        allows_shared=_ALLOWS_SHARED[uid],
    )


def _visible(gs, uid: str) -> set[tuple[str, str]]:
    frag, params = visible_cypher_fragment(_member(uid), alias="n")
    rows = gs.query(
        f"MATCH (n) WHERE {frag} RETURN labels(n) AS l, n.lumogis_id AS lid",
        params,
    )
    return {(r["l"][0], r["lid"]) for r in rows}


def _seed_personal(w, owner: str = "alice") -> dict[str, str]:
    """One each of Document, entity (mentioned in that Document), Session, Note, AudioMemo."""
    ids = {
        "doc": f"/uploads/{owner}/{uuid.uuid4().hex[:6]}.md",
        "entity": str(uuid.uuid4()),
        "session": str(uuid.uuid4()),
        "note": str(uuid.uuid4()),
        "audio": str(uuid.uuid4()),
    }
    graph_writer.project_document(
        w.gs, file_path=ids["doc"], file_type="md", user_id=owner, ms=w.ms
    )
    graph_writer.project_entity(
        w.gs,
        entity_id=ids["entity"],
        entity_type="PERSON",
        name="Ada",
        evidence_id=ids["doc"],
        evidence_type="DOCUMENT",
        user_id=owner,
        ms=w.ms,
    )
    # entities=[] / entity_ids=None: a non-empty list would resolve names via Postgres.
    graph_writer.project_session(
        w.gs,
        session_id=ids["session"],
        summary="s",
        topics=[],
        entities=[],
        entity_ids=None,
        user_id=owner,
        ms=w.ms,
    )
    graph_writer.project_note(w.gs, note_id=ids["note"], user_id=owner, ms=w.ms)
    graph_writer.project_audio(
        w.gs, audio_id=ids["audio"], file_path="/a.wav", user_id=owner, ms=w.ms
    )
    return ids


def test_owner_sees_own_personal_nodes_via_stats_route(world):
    ids = _seed_personal(world)
    # The entity's Document stub MERGEs onto the project_document node: 5, not 6.
    # Before LUM-961 this was 0.
    assert world.stats("alice")["node_count"] == 5
    assert _visible(world.gs, "alice") == {
        ("Document", ids["doc"]),
        ("Person", ids["entity"]),
        ("Session", ids["session"]),
        ("Note", ids["note"]),
        ("AudioMemo", ids["audio"]),
    }


def test_other_member_sees_none_of_the_owners_personal_nodes(world):
    ids = _seed_personal(world)
    assert world.stats("bob")["node_count"] == 0
    assert _visible(world.gs, "bob") == set()
    # mention_sources builds its own UserContext(user_id=…): unauthenticated (no admin
    # bypass), allows_shared=None → resolved through the fake's users lookup, as in prod.
    assert graph_query.mention_sources(world.gs, ids["entity"], "bob")["sources"] == []


def test_mention_sources_returns_the_document_for_the_owner(world):
    ids = _seed_personal(world)
    sources = graph_query.mention_sources(world.gs, ids["entity"], "alice")["sources"]
    assert [s["source_id"] for s in sources] == [ids["doc"]]


def test_reprojection_is_idempotent_and_keeps_scope(world):
    fp = f"/uploads/alice/{uuid.uuid4().hex[:6]}.md"
    for _ in range(2):
        graph_writer.project_document(
            world.gs, file_path=fp, file_type="md", user_id="alice", ms=world.ms
        )
    rows = world.gs.query(
        "MATCH (d:Document {lumogis_id: $fp}) RETURN count(d) AS c, collect(d.scope) AS s",
        {"fp": fp},
    )
    assert (rows[0]["c"], rows[0]["s"]) == (1, ["personal"])


def _publish_note_and_session(w, owner: str, scope: str = "shared") -> dict[str, str]:
    src_note, src_session = str(uuid.uuid4()), str(uuid.uuid4())
    proj = {"note": str(uuid.uuid4()), "session": str(uuid.uuid4())}
    graph_writer.project_note(w.gs, note_id=src_note, user_id=owner, ms=w.ms)
    graph_writer.project_session(
        w.gs, session_id=src_session, summary="s", topics=[], entities=[], user_id=owner, ms=w.ms
    )
    graph_writer.project_note(
        w.gs, note_id=proj["note"], user_id=owner, scope=scope, published_from=src_note, ms=w.ms
    )
    graph_writer.project_session(
        w.gs,
        session_id=proj["session"],
        summary="s",
        topics=[],
        entities=[],
        user_id=owner,
        scope=scope,
        published_from=src_session,
        ms=w.ms,
    )
    return proj


def test_shared_projection_visible_to_household_not_to_opted_out_member(world):
    proj = _publish_note_and_session(world, "alice")
    assert world.stats("bob")["node_count"] == 2  # allows_shared True
    assert world.stats("dana")["node_count"] == 0  # allows_shared False
    assert world.stats("alice")["node_count"] == 4  # her 2 personal nodes + the 2 projections
    assert _visible(world.gs, "bob") == {("Note", proj["note"]), ("Session", proj["session"])}
    assert _visible(world.gs, "dana") == set()


def test_system_projection_visible_to_opted_out_member(world):
    proj = _publish_note_and_session(world, "alice", scope="system")
    assert world.stats("dana")["node_count"] == 2
    assert _visible(world.gs, "dana") == {("Note", proj["note"]), ("Session", proj["session"])}


def test_personal_and_projection_nodes_do_not_collide(world):
    src = str(uuid.uuid4())
    proj = str(uuid.uuid4())
    graph_writer.project_note(world.gs, note_id=src, user_id="alice", ms=world.ms)
    graph_writer.project_note(
        world.gs, note_id=proj, user_id="alice", scope="shared", published_from=src, ms=world.ms
    )
    rows = world.gs.query(
        "MATCH (n:Note) RETURN n.lumogis_id AS lid, n.scope AS scope, n.published_from AS pf"
    )
    assert {(r["lid"], r["scope"], r["pf"]) for r in rows} == {
        (src, "personal", None),
        (proj, "shared", src),
    }
    # The personal one stays invisible to bob; only the projection is household-visible.
    assert _visible(world.gs, "bob") == {("Note", proj)}
