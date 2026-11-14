# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-586: the shared-edge sweep must match the REAL personal-node model.

Regression guard for the P0 the review found: personal entity nodes are written
by ``graph/writer.py::project_entity`` with a concrete TYPE label (``:Person``…)
keyed on ``(lumogis_id, user_id)``. The historical sweep matched
``(:Entity {scope:'personal'})`` — a pattern no personal node satisfies — so it
silently created zero shared edges.

LUM-961: personal nodes now carry ``scope = 'personal'`` and the sweep selects
them by it (strictly: a node without ``scope`` is a pre-fix leftover and is
ignored; there is no ``IS NULL`` fallback).
"""

from __future__ import annotations

from conftest import MockGraphStore
from projection import _sweep_incident_edges

_actor = "user-586"
_src = "src-entity-586"
_proj = "proj-entity-586"


def _sweep_cypher() -> str:
    store = MockGraphStore()
    _sweep_incident_edges(
        store,
        src_entity_id=_src,
        proj_entity_id=_proj,
        target_scope="shared",
        actor_user_id=_actor,
    )
    assert len(store.queries) == 1
    return store.queries[0][0]


def test_sweep_does_not_match_personal_nodes_by_entity_scope_label():
    cypher = _sweep_cypher()
    # The broken pattern must be gone in BOTH the source and neighbour matches.
    assert "scope: 'personal'" not in cypher
    assert "{lumogis_id: $src_id, scope: 'personal'}" not in cypher


def test_sweep_matches_personal_nodes_label_agnostically_by_personal_scope():
    cypher = _sweep_cypher()
    # Personal source + neighbour are selected by scope = 'personal' (LUM-961),
    # not a concrete/`:Entity` label and not by property absence.
    assert "MATCH (src {lumogis_id: $src_id})" in cypher
    assert "src.scope = 'personal'" in cypher
    assert "MATCH (src)-[r:RELATES_TO]-(other)" in cypher
    assert "other.scope = 'personal'" in cypher
    assert "IS NULL" not in cypher


def test_sweep_returns_the_number_of_edges_it_merged():
    class _Store(MockGraphStore):
        def query(self, cypher, params=None):
            super().query(cypher, params)
            return [{"swept": 3}]

    swept = _sweep_incident_edges(
        _Store(),
        src_entity_id=_src,
        proj_entity_id=_proj,
        target_scope="shared",
        actor_user_id=_actor,
    )
    assert swept == 3


def test_sweep_with_no_rows_returns_zero():
    assert (
        _sweep_incident_edges(
            MockGraphStore(),
            src_entity_id=_src,
            proj_entity_id=_proj,
            target_scope="shared",
            actor_user_id=_actor,
        )
        == 0
    )


def test_sweep_projection_nodes_keep_entity_scope_shape():
    cypher = _sweep_cypher()
    # The shared/system projection nodes DO carry :Entity + scope (as written by
    # project_entity_into_graph).
    assert "MATCH (proj:Entity {lumogis_id: $proj_id, scope: $target_scope})" in cypher
    assert "other_proj:Entity" in cypher
    assert "published_from: other.lumogis_id" in cypher
