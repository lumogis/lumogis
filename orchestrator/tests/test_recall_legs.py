# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for individual recall legs + their service helpers (LUM-295).

Asserts the exact parameterised SQL shapes and the Qdrant filter shape (the
flat-dict filter is the P0 cross-user leak — these guard it), plus the new
entities/entity_edges read helpers.
"""

from datetime import datetime
from datetime import timezone

import pytest

from services import recall as R

AS_OF = datetime(2026, 6, 25, tzinfo=timezone.utc)


class FakeMS:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def fetch_all(self, query, params=None):
        self.calls.append((query, params))
        return self._results.pop(0) if self._results else []


class FakeEmbedder:
    def embed(self, text):
        return [0.1, 0.2, 0.3]


# --- semantic leg -----------------------------------------------------------


def test_leg_semantic_when_hit_then_reads_payload_memory_id_not_point_id():
    class FakeVS:
        def __init__(self):
            self.call = None

        def search(self, collection, vector, limit, threshold, filter=None, sparse_query=None):
            self.call = {"collection": collection, "threshold": threshold, "filter": filter}
            return [{"id": "qpoint", "score": 0.9, "payload": {"memory_id": "m1", "user_id": "u", "bank": "coding"}}]

    vs = FakeVS()
    out = R._leg_semantic("q", user_id="u", bank="coding", embedder=FakeEmbedder(), vs=vs)
    assert out == ["m1"]  # payload memory_id, not the qdrant point id
    assert vs.call["threshold"] == 0.0
    # Load-bearing: the explicit must-filter shape (a flat dict = no filter = cross-user leak).
    assert vs.call["filter"] == {
        "must": [
            {"key": "user_id", "match": {"value": "u"}},
            {"key": "bank", "match": {"value": "coding"}},
        ]
    }


def test_leg_semantic_cross_user_isolation_through_mock_vector_store():
    """Drive the conftest MockVectorStore end-to-end: only the caller's memory survives.

    MockVectorStore honours the {"must":[{"key":..}]} shape; this proves the filter
    actually filters (not just that call-args were passed).
    """
    from tests._fakes import MockVectorStore  # standalone — no heavy conftest import

    vs = MockVectorStore()
    vs.create_collection("memories", 3)
    vs.upsert("memories", "p-mine", [0.1, 0.2, 0.3], {"memory_id": "mine", "user_id": "u", "bank": "coding"})
    vs.upsert("memories", "p-other", [0.1, 0.2, 0.3], {"memory_id": "other", "user_id": "intruder", "bank": "coding"})

    out = R._leg_semantic("q", user_id="u", bank="coding", embedder=FakeEmbedder(), vs=vs)
    assert out == ["mine"]  # the intruder's memory is filtered out by the must-filter


# --- bm25 leg ---------------------------------------------------------------


def test_leg_bm25_query_shape_and_params():
    ms = FakeMS([[{"id": "m2"}, {"id": "m3"}]])
    out = R._leg_bm25("LUM-291 origin guard", user_id="u", bank="coding", as_of=AS_OF, ms=ms)
    assert out == ["m2", "m3"]
    q, params = ms.calls[0]
    assert "websearch_to_tsquery('english', %s)" in q
    assert "ts_rank_cd(content_tsv, websearch_to_tsquery('english', %s)) DESC" in q
    assert "(valid_until IS NULL OR valid_until >= %s)" in q
    assert params[0] == "u" and params[1] == "coding" and params[2] == AS_OF


# --- temporal leg -----------------------------------------------------------


def test_leg_temporal_orders_by_valid_from_desc_and_filters_validity():
    ms = FakeMS([[{"id": "m9"}, {"id": "m8"}]])
    out = R._leg_temporal(user_id="u", bank="coding", as_of=AS_OF, ms=ms)
    assert out == ["m9", "m8"]
    q = ms.calls[0][0]
    assert "ORDER BY valid_from DESC" in q
    assert "(valid_until IS NULL OR valid_until >= %s)" in q


def test_leg_semantic_when_cross_bank_then_omits_bank_filter():
    class FakeVS:
        def __init__(self):
            self.call = None

        def search(self, collection, vector, limit, threshold, filter=None, sparse_query=None):
            self.call = filter
            return []

    vs = FakeVS()
    R._leg_semantic("q", user_id="u", bank="*", embedder=FakeEmbedder(), vs=vs)
    assert vs.call == {"must": [{"key": "user_id", "match": {"value": "u"}}]}


def test_leg_bm25_when_cross_bank_then_omits_bank_clause():
    ms = FakeMS([[{"id": "m1"}]])
    R._leg_bm25("q", user_id="u", bank="*", as_of=AS_OF, ms=ms)
    q, params = ms.calls[0]
    assert "bank = %s" not in q
    assert params[0] == "u"


# --- entities.entity_ids_for_query ------------------------------------------


def test_entity_ids_for_query_returns_ids_user_scoped():
    from services import entities

    ms = FakeMS([[{"entity_id": "e1"}, {"entity_id": "e2"}]])
    out = entities.entity_ids_for_query("alice bob", user_id="u", limit=10, ms=ms)
    assert out == ["e1", "e2"]
    q, params = ms.calls[0]
    assert "SELECT entity_id FROM entities" in q
    assert "user_id = %s" in q and "name ILIKE ANY(%s)" in q
    assert "ORDER BY mention_count DESC" in q
    assert params[0] == "u"


def test_entity_ids_for_query_when_blank_then_empty_without_query():
    from services import entities

    ms = FakeMS([])
    assert entities.entity_ids_for_query("  ", user_id="u", ms=ms) == []
    assert ms.calls == []


# --- entity_edges.memories_for_entities -------------------------------------


def test_memories_for_entities_query_shape():
    from services import entity_edges

    ms = FakeMS([[{"evidence_id": "m1"}, {"evidence_id": "m2"}]])
    out = entity_edges.memories_for_entities(
        ["e1", "e2"], user_id="u", bank="coding", as_of=AS_OF, ms=ms
    )
    assert out == ["m1", "m2"]
    q, params = ms.calls[0]
    assert "SELECT DISTINCT evidence_id FROM entity_edges" in q
    assert "(src_entity_id = ANY(%s) OR dst_entity_id = ANY(%s))" in q
    assert "evidence_id IS NOT NULL" in q
    assert "(valid_until IS NULL OR valid_until >= %s)" in q
    assert params[0] == "u" and params[1] == "coding"


def test_memories_for_entities_when_no_seeds_then_empty_without_query():
    from services import entity_edges

    ms = FakeMS([])
    assert entity_edges.memories_for_entities([], user_id="u", bank="coding", as_of=AS_OF, ms=ms) == []
    assert ms.calls == []


def test_memories_for_entities_when_hops_gt_1_then_not_implemented():
    from services import entity_edges

    with pytest.raises(NotImplementedError):
        entity_edges.memories_for_entities(["e1"], user_id="u", bank="coding", as_of=AS_OF, hops=2)
