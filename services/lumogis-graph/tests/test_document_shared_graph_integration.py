# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live-FalkorDB merge gate for graph-aware entity sharing (LUM-586).

This is the DoD gate the hermetic sweep tests
(``test_projection_shared_sweep_lum586.py``) cannot cover: it runs against a
**real FalkorDB** and proves the backward edge sweep actually MERGEs a shared
``RELATES_TO`` edge between two shared projection nodes — the P0 that the old
``(:Entity {scope:'personal'})`` match silently dropped (personal nodes are
label-agnostic: a type label plus ``scope = 'personal'``, LUM-961).

This module is in no make target (``compose-test-kg-integration`` runs only
``test_temporal_integration.py``). Run it by hand against a disposable FalkorDB:
``KG_INTEGRATION_FALKORDB=1 FALKORDB_URL=redis://127.0.0.1:16379 python -m pytest
-q tests/test_document_shared_graph_integration.py``. The CI ``M1 compat`` sweep
tests in ``orchestrator/tests/premium/test_graph_writer.py`` cover the same Cypher.

Runs against a live FalkorDB when ``KG_INTEGRATION_FALKORDB=1`` and
``FALKORDB_URL`` point at the disposable test stack
(``make compose-test-kg-integration``); skipped in the mocked
``make compose-test-kg`` path.

Coverage:
  * two shared endpoints → a shared RELATES_TO edge appears (sweep fix)
  * only one endpoint shared → NO shared edge (two-shared-endpoints rule)
  * personal nodes matched by ``scope = 'personal'`` (LUM-961); nodes without
    ``scope`` are ignored by the sweep (no shared edge)
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


def _real_graph_or_skip():
    try:
        import config

        gs = config.get_graph_store()
        if (
            gs is None
            or type(gs).__name__ == "MockGraphStore"
            or not hasattr(gs, "create_node")
            or not gs.ping()
        ):
            pytest.skip("real FalkorDB unavailable")
        return gs
    except Exception:
        pytest.skip("real FalkorDB unavailable")


def _seed_personal_pair(gs, *, e1: str, e2: str, user_id: str, scoped: bool = True) -> None:
    """Two personal entity nodes with a personal RELATES_TO edge.

    Personal nodes carry ``scope = 'personal'`` (LUM-961) — the shape
    ``graph/writer.py::project_entity`` writes and the sweep matches. Label is
    intentionally NOT ``:Entity`` to prove the sweep is label-agnostic on the
    personal side. ``scoped=False`` seeds the pre-LUM-961 shape (no ``scope``).
    """
    set_scope = "SET a.scope = 'personal', b.scope = 'personal' " if scoped else ""
    gs.query(
        "MERGE (a:Person {lumogis_id: $e1, user_id: $u}) "
        "MERGE (b:Person {lumogis_id: $e2, user_id: $u}) "
        f"{set_scope}"
        "MERGE (a)-[r:RELATES_TO {user_id: $u}]->(b) "
        "SET r.co_occurrence_count = 2, r.evidence_id = $ev "
        "RETURN id(r) AS rid",
        {"e1": e1, "e2": e2, "u": user_id, "ev": f"ev-{uuid.uuid4().hex[:6]}"},
    )


def _project(gs, *, src: str, proj: str, user_id: str) -> bool:
    import projection

    return projection.project_entity_into_graph(
        src_entity_id=src,
        proj_entity_id=proj,
        target_scope="shared",
        actor_user_id=user_id,
        name=f"name-{src}",
        entity_type="PERSON",
    )


def _shared_edge_count(gs, *, e1: str, e2: str, user_id: str) -> int:
    rows = gs.query(
        "MATCH (a:Entity {scope: 'shared', user_id: $u})"
        "-[r:RELATES_TO {scope: 'shared'}]-"
        "(b:Entity {scope: 'shared', user_id: $u}) "
        "WHERE a.published_from = $e1 AND b.published_from = $e2 "
        "RETURN count(r) AS cnt",
        {"e1": e1, "e2": e2, "u": user_id},
    )
    return int(rows[0]["cnt"]) if rows else 0


def _cleanup(gs, *, user_id: str) -> None:
    try:
        gs.query("MATCH (n {user_id: $u}) DETACH DELETE n", {"u": user_id})
    except Exception:
        pass


def test_document_shared_projects_edge_between_two_shared_entities():
    gs = _real_graph_or_skip()
    user_id = f"lum586_{uuid.uuid4().hex[:8]}"
    e1, e2 = f"{user_id}_e1", f"{user_id}_e2"
    proj1, proj2 = f"{user_id}_p1", f"{user_id}_p2"
    try:
        _seed_personal_pair(gs, e1=e1, e2=e2, user_id=user_id)

        # Project the FIRST endpoint — no shared edge yet (other end not shared).
        assert _project(gs, src=e1, proj=proj1, user_id=user_id) is True
        assert _shared_edge_count(gs, e1=e1, e2=e2, user_id=user_id) == 0

        # Project the SECOND endpoint — the backward sweep now finds both
        # projections and MERGEs the shared edge (the P0 fix).
        assert _project(gs, src=e2, proj=proj2, user_id=user_id) is True
        assert _shared_edge_count(gs, e1=e1, e2=e2, user_id=user_id) == 1

        # Idempotent: re-projecting does not duplicate the edge.
        assert _project(gs, src=e2, proj=proj2, user_id=user_id) is True
        assert _shared_edge_count(gs, e1=e1, e2=e2, user_id=user_id) == 1
    finally:
        _cleanup(gs, user_id=user_id)


def test_shared_edge_requires_two_shared_endpoints():
    gs = _real_graph_or_skip()
    user_id = f"lum586_{uuid.uuid4().hex[:8]}"
    e1, e2 = f"{user_id}_e1", f"{user_id}_e2"
    proj1 = f"{user_id}_p1"
    try:
        _seed_personal_pair(gs, e1=e1, e2=e2, user_id=user_id)
        # Only e1 is shared; e2 has no projection → no shared edge may form.
        assert _project(gs, src=e1, proj=proj1, user_id=user_id) is True
        assert _shared_edge_count(gs, e1=e1, e2=e2, user_id=user_id) == 0
    finally:
        _cleanup(gs, user_id=user_id)


def test_unscoped_personal_nodes_get_no_shared_edge():
    """Strict sweep (LUM-961): pre-fix nodes without ``scope`` are ignored, not matched."""
    gs = _real_graph_or_skip()
    user_id = f"lum961_{uuid.uuid4().hex[:8]}"
    e1, e2 = f"{user_id}_e1", f"{user_id}_e2"
    try:
        _seed_personal_pair(gs, e1=e1, e2=e2, user_id=user_id, scoped=False)
        assert _project(gs, src=e1, proj=f"{user_id}_p1", user_id=user_id) is True
        assert _project(gs, src=e2, proj=f"{user_id}_p2", user_id=user_id) is True
        assert _shared_edge_count(gs, e1=e1, e2=e2, user_id=user_id) == 0
    finally:
        _cleanup(gs, user_id=user_id)
