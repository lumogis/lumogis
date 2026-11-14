# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Hermetic tests for LUM-208 RELATES_TO projection sweep Cypher."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import MockGraphStore
from graph.relates_to_merge_patterns import DIRECTED_MERGE_RELATES_TO
from graph.relates_to_merge_patterns import UNDIRECTED_MERGE_RELATES_TO
from projection import _sweep_incident_edges

_actor = "user-208"
_src = "src-entity-208"
_proj = "proj-entity-208"


def _call_sweep(store: MockGraphStore, *, scope: str = "shared") -> tuple[str, dict]:
    _sweep_incident_edges(
        store,
        src_entity_id=_src,
        proj_entity_id=_proj,
        target_scope=scope,
        actor_user_id=_actor,
    )
    assert len(store.queries) == 1
    return store.queries[0]


def test_sweep_incident_edges_merge_is_directed():
    store = MockGraphStore()
    cypher, _params = _call_sweep(store)
    assert not UNDIRECTED_MERGE_RELATES_TO.search(cypher)
    assert DIRECTED_MERGE_RELATES_TO.search(cypher)


def test_sweep_merge_pattern_omits_evidence_id_key():
    store = MockGraphStore()
    cypher, _params = _call_sweep(store)
    merge_part = cypher.split("MERGE", 1)[1].split("SET", 1)[0]
    assert "[r2:RELATES_TO { evidence_id:" not in cypher
    assert "RELATES_TO { evidence_id:" not in merge_part


def test_sweep_cypher_has_no_evidence_id_null_filter():
    store = MockGraphStore()
    cypher, _params = _call_sweep(store)
    assert "WHERE r.evidence_id IS NOT NULL" not in cypher


def test_sweep_cypher_keeps_relationship_in_scope():
    store = MockGraphStore()
    cypher, _params = _call_sweep(store)
    assert re.search(
        r"WITH[\s\S]*?\br\b[\s\S]*?MERGE\s*\(lower_node\)",
        cypher,
        re.DOTALL,
    )


def test_sweep_contains_canonical_case_expression():
    store = MockGraphStore()
    cypher, _params = _call_sweep(store)
    assert "CASE WHEN proj.lumogis_id < other_proj.lumogis_id" in cypher


def test_sweep_cypher_constrains_user_ids():
    store = MockGraphStore()
    cypher, _params = _call_sweep(store)
    before_set, _sep, set_part = cypher.partition("SET ")
    assert before_set.count("user_id = $actor") == 4
    assert "r2.user_id = $actor" in set_part


def test_sweep_query_params_complete():
    store = MockGraphStore()
    _cypher, params = _call_sweep(store)
    assert params == {
        "src_id": _src,
        "proj_id": _proj,
        "target_scope": "shared",
        "actor": _actor,
    }


def test_sweep_emits_expected_edge_properties():
    store = MockGraphStore()
    cypher, _params = _call_sweep(store)
    tail = cypher.split("SET", 1)[1]
    for name in (
        "co_occurrence_count",
        "user_id",
        "evidence_id",
        "published_from_evidence",
    ):
        assert name in tail


def test_sweep_no_incident_edges_is_noop():
    store = MockGraphStore()
    _sweep_incident_edges(
        store,
        src_entity_id=_src,
        proj_entity_id=_proj,
        target_scope="shared",
        actor_user_id=_actor,
    )
    assert len(store.queries) == 1


def test_sweep_bad_scope_raises():
    store = MockGraphStore()
    with pytest.raises(ValueError, match="target_scope"):
        _sweep_incident_edges(
            store,
            src_entity_id=_src,
            proj_entity_id=_proj,
            target_scope="personal",
            actor_user_id=_actor,
        )
    assert store.queries == []


def test_private_kg_reference_mentions_falkor_merge_constraint():
    here = Path(__file__).resolve()
    path: Path | None = None
    for parent in [here.parent, *here.parents]:
        candidate = parent / "docs" / "private" / "kg" / "kg_reference.md"
        if candidate.is_file():
            path = candidate
            break
    if path is None:
        pytest.skip(
            "kg_reference.md not on PYTHONPATH (e.g. lumogis-graph:test image "
            "ships only services/lumogis-graph/)"
        )
    text = path.read_text(encoding="utf-8")
    assert "directed" in text.lower()
    assert "falkordb" in text.lower()
