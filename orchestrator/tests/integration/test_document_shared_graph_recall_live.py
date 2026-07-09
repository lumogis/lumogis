# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live E2E: document share → entity cascade → DOCUMENT_SHARED → member B graph recall (LUM-601).

Closes the gap left by the split suites:

* ``test_document_shared_entity_cascade_live.py`` — Postgres+Qdrant refcount only
  (``DOCUMENT_SHARED`` is intentionally a no-op there).
* ``test_document_shared_graph_integration.py`` — FalkorDB sweep only (manual
  ``project_entity_into_graph``, no orchestrator cascade or member recall).

This test runs the **full publish path** in-process: personal entities +
personal ``RELATES_TO`` seed → ``cascade_share_document_entities`` (Postgres +
Qdrant + hook fire) → ``graph.writer.on_document_shared`` (shared graph MERGE) →
member B ``query_graph_tool`` ego recall via scope-union visibility.

Skips when Postgres, Qdrant, or FalkorDB are unreachable (plain host unit run).
Primary gate: ``make compose-test-integration`` with the FalkorDB overlay.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest
from auth import UserContext

import config
import hooks
from events import Event
from services import document_entity_cascade as cascade
from services import shared_items as shared_items_svc

pytestmark = pytest.mark.integration

ENTITIES = "entities"


def _vector_dim() -> int:
    try:
        return int(config.get_embedder().vector_size)
    except Exception:
        return 768


def _live_stack_or_skip(monkeypatch):
    """Wire real Postgres + Qdrant + FalkorDB; register sync DOCUMENT_SHARED handler."""
    url = os.environ.get("FALKORDB_URL", "redis://falkordb:6379")
    if not url:
        pytest.skip("FALKORDB_URL not set — run with docker-compose.falkordb.yml overlay")

    monkeypatch.setenv("GRAPH_BACKEND", "falkordb")
    monkeypatch.setenv("FALKORDB_URL", url)
    monkeypatch.setattr(config, "get_graph_mode", lambda: "service")

    for key in list(config._instances.keys()):
        if key.startswith("graph_store:") or key == "graph_store":
            config._instances.pop(key, None)

    try:
        from adapters.postgres_store import PostgresStore
        from adapters.qdrant_store import QdrantStore

        vs = QdrantStore(url=os.environ.get("QDRANT_URL", "http://qdrant:6333"))
        ms = PostgresStore(
            host=os.environ.get("POSTGRES_HOST", "postgres"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            user=os.environ.get("POSTGRES_USER", "lumogis"),
            password=os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
            dbname=os.environ.get("POSTGRES_DB", "lumogis"),
        )
        if not ms.ping():
            raise RuntimeError("postgres ping failed")
        try:
            vs.count(ENTITIES)
        except Exception:
            vs.create_collection(ENTITIES, _vector_dim())
            vs.count(ENTITIES)

        gs = config.get_graph_store("personal")
        if gs is None or not gs.ping():
            raise RuntimeError("FalkorDB graph store unavailable")
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"live stack not reachable: {exc}")

    monkeypatch.setitem(config._instances, "vector_store", vs)
    monkeypatch.setitem(config._instances, "metadata_store", ms)

    # Lumogis-graph sources resolve through orchestrator ``config`` (in-process wiring).
    import plugins.graph  # noqa: F401 — sys.path for graph.*
    import graph.writer as graph_writer

    hooks.register(Event.DOCUMENT_SHARED, graph_writer.on_document_shared)
    monkeypatch.setattr(hooks, "fire_background", hooks.fire)

    return ms, vs, gs


@pytest.fixture
def live_graph_e2e(monkeypatch):
    yield _live_stack_or_skip(monkeypatch)


def _seed_personal_entity(ms, owner: str, name: str) -> str:
    row = ms.fetch_one(
        "INSERT INTO entities (name, entity_type, user_id, scope) "
        "VALUES (%s, 'PERSON', %s, 'personal') RETURNING entity_id",
        (name, owner),
    )
    return str(row["entity_id"])


def _relate_to_document(ms, owner: str, entity_id: str, file_path: str) -> None:
    ms.execute(
        "INSERT INTO entity_relations "
        "(source_id, relation_type, evidence_type, evidence_id, user_id) "
        "VALUES (%s, 'MENTIONED_IN_DOCUMENT', 'DOCUMENT', %s, %s) "
        "ON CONFLICT DO NOTHING",
        (entity_id, file_path, owner),
    )


def _seed_personal_graph_pair(gs, *, e1: str, e2: str, user_id: str) -> None:
    """Personal RELATES_TO edge with co_occurrence_count at the query threshold."""
    gs.query(
        "MERGE (a:Person {lumogis_id: $e1, user_id: $u}) "
        "MERGE (b:Person {lumogis_id: $e2, user_id: $u}) "
        "MERGE (a)-[r:RELATES_TO {user_id: $u}]->(b) "
        "SET r.co_occurrence_count = 3, r.evidence_id = $ev "
        "RETURN id(r) AS rid",
        {"e1": e1, "e2": e2, "u": user_id, "ev": f"ev-{uuid.uuid4().hex[:6]}"},
    )


def _cleanup(ms, vs, gs, owner: str) -> None:
    try:
        gs.query("MATCH (n {user_id: $u}) DETACH DELETE n", {"u": owner})
    except Exception:
        pass
    for stmt, args in (
        ("DELETE FROM entity_relations WHERE user_id = %s", (owner,)),
        ("DELETE FROM file_index WHERE user_id = %s", (owner,)),
        ("DELETE FROM entities WHERE user_id = %s", (owner,)),
    ):
        try:
            ms.execute(stmt, args)
        except Exception:
            pass
    try:
        vs.delete_where(ENTITIES, {"must": [{"key": "user_id", "match": {"value": owner}}]})
    except Exception:
        pass


def test_publish_doc_projects_entities_to_shared_graph(live_graph_e2e):
    """LUM-601 — full share path through graph recall for a second household member."""
    ms, vs, gs = live_graph_e2e
    import graph.query as graph_query

    owner = f"lum601-owner-{uuid.uuid4().hex[:8]}"
    member = f"lum601-member-{uuid.uuid4().hex[:8]}"
    suffix = uuid.uuid4().hex[:6]
    name_a = f"Lum601Alpha_{suffix}"
    name_b = f"Lum601Beta_{suffix}"
    file_path = f"/uploads/{owner}/lum601-{suffix}.md"
    actor = UserContext(user_id=owner, is_authenticated=True)

    try:
        e1 = _seed_personal_entity(ms, owner, name_a)
        e2 = _seed_personal_entity(ms, owner, name_b)
        _relate_to_document(ms, owner, e1, file_path)
        _relate_to_document(ms, owner, e2, file_path)
        _seed_personal_graph_pair(gs, e1=e1, e2=e2, user_id=owner)

        # Negative: member B cannot resolve the entity before the document is shared.
        pre = json.loads(
            graph_query.query_graph_tool(
                {"mode": "ego", "entity": name_a, "user_id": member, "limit": 10}
            )
        )
        assert pre.get("found") is False, "member B saw owner's entity before share"

        projected, failed = cascade.cascade_share_document_entities(
            src_file={"file_path": file_path, "user_id": owner}, actor=actor
        )
        assert (projected, failed) == (2, 0)

        entity_items = [
            i for i in shared_items_svc.list_my_shared_items(owner) if i["resource_type"] == "entities"
        ]
        shared_src_ids = {i["resource_id"] for i in entity_items}
        assert {e1, e2}.issubset(shared_src_ids), "owner shared-items list missing cascaded entities"

        post = json.loads(
            graph_query.query_graph_tool(
                {"mode": "ego", "entity": name_a, "user_id": member, "limit": 10}
            )
        )
        assert post.get("found") is True, post
        neighbor_names = {e.get("neighbor_name") for e in post.get("neighbors") or []}
        assert name_b in neighbor_names, (
            f"member B ego recall missing shared neighbor; got neighbors={post.get('neighbors')}"
        )
    finally:
        _cleanup(ms, vs, gs, owner)
