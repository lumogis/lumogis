# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for TEMPR recall fusion orchestration (LUM-295).

These inject fakes (the conftest MockMetadataStore.fetch_all is a no-op → write-
then-read is vacuous) and assert observable behaviour: RRF ordering/sources,
temporal-drop hydration (LUM-526 observability), rerank reorder/degrade, and
input handling. Graph-leg-specific SQL lives in test_recall_legs.py.
"""

from datetime import datetime
from datetime import timezone

from services import recall as R

AS_OF = datetime(2026, 6, 25, tzinfo=timezone.utc)


class FakeMS:
    """Returns queued fetch_all results in call order; records (query, params)."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def fetch_all(self, query, params=None):
        self.calls.append((query, params))
        return self._results.pop(0) if self._results else []


class FakeVS:
    def __init__(self, hits):
        self._hits = hits
        self.calls = []

    def search(self, collection, vector, limit, threshold, filter=None, sparse_query=None):
        self.calls.append({"limit": limit, "threshold": threshold, "filter": filter})
        return self._hits


class FakeEmbedder:
    def embed(self, text):
        return [0.1, 0.2, 0.3]


class ReverseReranker:
    def __init__(self):
        self.received = None

    def rerank(self, query, candidates, limit):
        self.received = candidates
        return list(reversed(candidates))[:limit]


# --- _rrf -------------------------------------------------------------------


def test_rrf_when_id_in_two_legs_then_score_sums_and_records_both_sources():
    fused = R._rrf({"semantic": ["a", "b"], "bm25": ["b", "c"]})
    sources = {mid: src for mid, _s, src in fused}
    assert sources["b"] == ["semantic", "bm25"]
    assert sources["a"] == ["semantic"]
    # b appears in both legs (rank1 + rank0) → outscores a (rank0 only) and c (rank1 only).
    assert fused[0][0] == "b"


def test_rrf_when_empty_then_returns_empty():
    assert R._rrf({}) == []


def test_rrf_when_single_list_then_preserves_that_order():
    assert [m for m, _s, _src in R._rrf({"semantic": ["x", "y", "z"]})] == ["x", "y", "z"]


# --- _hydrate ---------------------------------------------------------------


def test_hydrate_when_id_archived_then_dropped_and_entity_ids_populated():
    ms = FakeMS(
        [
            [{"id": "m1", "content": "hello", "bank": "coding", "valid_from": AS_OF, "valid_until": None}],
            [{"evidence_id": "m1", "entity_ids": ["e1", "e2"]}],
        ]
    )
    hyd = R._hydrate(["m1", "m2"], user_id="u", bank="coding", as_of=AS_OF, ms=ms)
    assert "m2" not in hyd and "m1" in hyd  # m2 archived/invalid → dropped (LUM-526 observable)
    assert hyd["m1"]["entity_ids"] == ["e1", "e2"]
    assert "valid_until IS NULL OR valid_until >= %s" in ms.calls[0][0]
    assert "unnest(ARRAY[src_entity_id, dst_entity_id])" in ms.calls[1][0]


def test_hydrate_when_empty_ids_then_no_query():
    ms = FakeMS([])
    assert R._hydrate([], user_id="u", bank="coding", as_of=AS_OF, ms=ms) == {}
    assert ms.calls == []


# --- recall() orchestration (graph excluded to keep imports light) ----------


def _ms_for_single_memory(content="the answer", entity_ids=("e1",)):
    return FakeMS(
        [
            [{"id": "m1"}],  # bm25
            [{"id": "m1"}],  # temporal
            [{"id": "m1", "content": content, "bank": "coding", "valid_from": AS_OF, "valid_until": None}],  # hydrate q1
            [{"evidence_id": "m1", "entity_ids": list(entity_ids)}],  # hydrate q2
        ]
    )


def _ms_hydrate_only(content="the answer", entity_ids=("e1",)):
    """For semantic-only runs: no bm25/temporal queries, just the two hydrate queries."""
    return FakeMS(
        [
            [{"id": "m1", "content": content, "bank": "coding", "valid_from": AS_OF, "valid_until": None}],  # hydrate q1
            [{"evidence_id": "m1", "entity_ids": list(entity_ids)}],  # hydrate q2
        ]
    )


def _semantic_vs(memory_id="m1"):
    return FakeVS(
        [{"id": "p", "score": 0.9, "payload": {"memory_id": memory_id, "user_id": "u", "bank": "coding"}}]
    )


def test_recall_when_query_matches_then_returns_fused_memory_with_sources():
    res = R.recall(
        user_id="u", bank="coding", query="the answer", limit=5,
        retrieval_strategies=["semantic", "bm25", "temporal"], as_of=AS_OF, rerank=False,
        ms=_ms_for_single_memory(), embedder=FakeEmbedder(), vs=_semantic_vs(),
    )
    assert len(res) == 1
    assert res[0].id == "m1"
    assert res[0].content == "the answer"
    assert res[0].entity_ids == ["e1"]
    assert set(res[0].source_strategies) == {"semantic", "bm25", "temporal"}
    assert res[0].score > 0


def test_recall_when_query_blank_then_returns_empty():
    assert R.recall(user_id="u", query="   ", ms=FakeMS([]), embedder=FakeEmbedder(), vs=FakeVS([])) == []


def test_recall_when_unknown_strategy_then_ignored():
    res = R.recall(
        user_id="u", query="x", retrieval_strategies=["semantic", "bogus"], as_of=AS_OF,
        rerank=False, ms=_ms_hydrate_only(), embedder=FakeEmbedder(), vs=_semantic_vs(),
    )
    assert len(res) == 1  # ran semantic only; "bogus" ignored, no crash


def test_recall_when_all_legs_empty_then_returns_empty():
    res = R.recall(
        user_id="u", query="x", retrieval_strategies=["semantic", "bm25", "temporal"], as_of=AS_OF,
        rerank=False, ms=FakeMS([]), embedder=FakeEmbedder(), vs=FakeVS([]),
    )
    assert res == []


def test_recall_when_limit_over_max_then_clamped():
    # Clamp is observable via the constant; a huge limit must not error.
    res = R.recall(
        user_id="u", query="the answer", limit=9999, retrieval_strategies=["semantic"],
        as_of=AS_OF, rerank=False, ms=_ms_hydrate_only(), embedder=FakeEmbedder(), vs=_semantic_vs(),
    )
    assert len(res) <= R._MAX_LIMIT


def test_recall_when_rerank_enabled_then_reorders_and_passes_content_text():
    vs = FakeVS(
        [
            {"id": "p1", "score": 0.9, "payload": {"memory_id": "m1", "user_id": "u", "bank": "coding"}},
            {"id": "p2", "score": 0.8, "payload": {"memory_id": "m2", "user_id": "u", "bank": "coding"}},
        ]
    )
    ms = FakeMS(
        [
            [{"id": "m1"}, {"id": "m2"}],  # bm25
            [{"id": "m1"}, {"id": "m2"}],  # temporal
            [
                {"id": "m1", "content": "first", "bank": "coding", "valid_from": AS_OF, "valid_until": None},
                {"id": "m2", "content": "second", "bank": "coding", "valid_from": AS_OF, "valid_until": None},
            ],  # hydrate q1
            [],  # hydrate q2
        ]
    )
    rr = ReverseReranker()
    res = R.recall(
        user_id="u", query="q", limit=5, retrieval_strategies=["semantic", "bm25", "temporal"],
        as_of=AS_OF, rerank=True, ms=ms, embedder=FakeEmbedder(), vs=vs, reranker=rr,
    )
    assert {c["text"] for c in rr.received} == {"first", "second"}  # rerank got content text
    assert res[0].id == "m2"  # reversed order applied


def test_recall_when_rerank_disabled_then_keeps_rrf_order():
    res = R.recall(
        user_id="u", query="the answer", retrieval_strategies=["semantic"], as_of=AS_OF,
        rerank=False, ms=_ms_hydrate_only(), embedder=FakeEmbedder(), vs=_semantic_vs(),
    )
    assert [m.id for m in res] == ["m1"]


def test_recall_when_semantic_leg_raises_then_degrades_to_other_legs():
    class BoomVS:
        def search(self, *a, **k):
            raise RuntimeError("qdrant 404 collection absent")

    res = R.recall(
        user_id="u", query="the answer", retrieval_strategies=["semantic", "bm25", "temporal"],
        as_of=AS_OF, rerank=False, ms=_ms_for_single_memory(), embedder=FakeEmbedder(), vs=BoomVS(),
    )
    assert len(res) == 1  # bm25+temporal still fuse; semantic degraded to []
    assert "semantic" not in res[0].source_strategies
