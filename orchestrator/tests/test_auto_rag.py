# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for LUM-308 auto-RAG retrieval and search_files dedupe."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from auth import UserContext
from models.search import SearchResult
from services.auto_rag import retrieve_document_context
from visibility import visible_qdrant_filter

from services import tools as tools_mod


def test_auto_rag_disabled_returns_empty(monkeypatch) -> None:
    import config as cfg

    monkeypatch.setattr(cfg, "get_auto_rag_enabled", lambda: False)
    called: dict[str, bool] = {"embed": False}

    def _no_embed(_q: str):
        called["embed"] = True
        return [0.1]

    monkeypatch.setattr(cfg, "get_embedder", lambda: MagicMock(embed=_no_embed))
    monkeypatch.setattr(cfg, "get_vector_store", lambda: MagicMock())
    out = retrieve_document_context("q", "u1")
    assert out == []
    assert called["embed"] is False


def test_auto_rag_no_qdrant_hits(monkeypatch) -> None:
    import config as cfg

    monkeypatch.setattr(cfg, "get_auto_rag_enabled", lambda: True)
    monkeypatch.setattr(cfg, "get_auto_rag_top_k_pre", lambda: 20)
    monkeypatch.setattr(cfg, "get_auto_rag_top_k_post", lambda: 3)
    monkeypatch.setattr(cfg, "get_auto_rag_min_rerank_score", lambda: 0.0)
    monkeypatch.setattr(cfg, "get_auto_rag_min_bi_encoder_score", lambda: 0.55)
    monkeypatch.setattr(cfg, "get_auto_rag_max_tokens", lambda: 512)
    monkeypatch.setattr(cfg, "get_embedder", lambda: MagicMock(embed=lambda _q: [0.0, 0.0]))
    monkeypatch.setattr(
        cfg,
        "get_vector_store",
        lambda: MagicMock(search=lambda **kw: []),
    )
    monkeypatch.setattr(cfg, "get_reranker", lambda: None)


def test_auto_rag_reranker_gate_drops_low_scores(monkeypatch) -> None:
    import config as cfg

    raw = [
        {
            "id": "a",
            "score": 0.9,
            "payload": {"text": "ta", "file_path": "/a", "scope": "personal"},
            "score_space": "cosine",
        },
        {
            "id": "b",
            "score": 0.8,
            "payload": {"text": "tb", "file_path": "/b", "scope": "personal"},
            "score_space": "cosine",
        },
    ]

    class _Rerank:
        def rerank(self, query, candidates, limit):
            for c in candidates:
                c["rerank_score"] = 0.9 if c["id"] == "a" else 0.01
            return sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)[:limit]

    monkeypatch.setattr(cfg, "get_auto_rag_enabled", lambda: True)
    monkeypatch.setattr(cfg, "get_auto_rag_top_k_pre", lambda: 20)
    monkeypatch.setattr(cfg, "get_auto_rag_top_k_post", lambda: 3)
    monkeypatch.setattr(cfg, "get_auto_rag_min_rerank_score", lambda: 0.5)
    monkeypatch.setattr(cfg, "get_auto_rag_min_bi_encoder_score", lambda: 0.55)
    monkeypatch.setattr(cfg, "get_auto_rag_max_tokens", lambda: 512)
    monkeypatch.setattr(cfg, "get_embedder", lambda: MagicMock(embed=lambda _q: [0.0]))
    monkeypatch.setattr(
        cfg,
        "get_vector_store",
        lambda: MagicMock(search=lambda **kw: list(raw)),
    )
    monkeypatch.setattr(cfg, "get_reranker", lambda: _Rerank())
    hits = retrieve_document_context("q", "u1")
    assert len(hits) == 1
    assert hits[0].point_id == "a"
    assert hits[0].score_kind == "rerank"


def test_auto_rag_no_reranker_hybrid_rrf_still_returns_hits(monkeypatch) -> None:
    import config as cfg

    raw = [
        {
            "id": "r1",
            "score": 0.04,
            "payload": {"text": "chunk", "file_path": "/x", "scope": "personal"},
            "score_space": "rrf",
        },
    ]
    monkeypatch.setattr(cfg, "get_auto_rag_enabled", lambda: True)
    monkeypatch.setattr(cfg, "get_auto_rag_top_k_pre", lambda: 20)
    monkeypatch.setattr(cfg, "get_auto_rag_top_k_post", lambda: 3)
    monkeypatch.setattr(cfg, "get_auto_rag_min_rerank_score", lambda: 0.0)
    monkeypatch.setattr(cfg, "get_auto_rag_min_bi_encoder_score", lambda: 0.55)
    monkeypatch.setattr(cfg, "get_auto_rag_max_tokens", lambda: 512)
    monkeypatch.setattr(cfg, "get_embedder", lambda: MagicMock(embed=lambda _q: [0.0]))
    monkeypatch.setattr(
        cfg,
        "get_vector_store",
        lambda: MagicMock(search=lambda **kw: list(raw)),
    )
    monkeypatch.setattr(cfg, "get_reranker", lambda: None)
    hits = retrieve_document_context("q", "u1")
    assert len(hits) == 1
    assert hits[0].score_kind == "rrf_gated"


def test_auto_rag_no_reranker_dense_uses_bi_floor(monkeypatch) -> None:
    import config as cfg

    raw_low = [
        {
            "id": "l",
            "score": 0.2,
            "payload": {"text": "low", "file_path": "/l", "scope": "personal"},
            "score_space": "cosine",
        },
    ]
    raw_high = [
        {
            "id": "h",
            "score": 0.9,
            "payload": {"text": "high", "file_path": "/h", "scope": "personal"},
            "score_space": "cosine",
        },
    ]

    def _vs(low: bool):
        class _VS:
            def search(self, **kw):
                return list(raw_low if low else raw_high)

        return _VS()

    monkeypatch.setattr(cfg, "get_auto_rag_enabled", lambda: True)
    monkeypatch.setattr(cfg, "get_auto_rag_top_k_pre", lambda: 20)
    monkeypatch.setattr(cfg, "get_auto_rag_top_k_post", lambda: 3)
    monkeypatch.setattr(cfg, "get_auto_rag_min_rerank_score", lambda: 0.0)
    monkeypatch.setattr(cfg, "get_auto_rag_min_bi_encoder_score", lambda: 0.55)
    monkeypatch.setattr(cfg, "get_auto_rag_max_tokens", lambda: 512)
    monkeypatch.setattr(cfg, "get_embedder", lambda: MagicMock(embed=lambda _q: [0.0]))
    monkeypatch.setattr(cfg, "get_reranker", lambda: None)

    monkeypatch.setattr(cfg, "get_vector_store", lambda: _vs(True))
    assert retrieve_document_context("q", "u1") == []

    monkeypatch.setattr(cfg, "get_vector_store", lambda: _vs(False))
    hits = retrieve_document_context("q", "u1")
    assert len(hits) == 1
    assert hits[0].point_id == "h"


def test_auto_rag_respects_max_tokens(monkeypatch) -> None:
    import config as cfg

    raw = [
        {
            "id": str(i),
            "score": 1.0 - i * 0.001,
            "payload": {
                "text": "wordword " * 15,
                "file_path": f"/f{i}",
                "scope": "personal",
            },
            "score_space": "rrf",
        }
        for i in range(12)
    ]
    monkeypatch.setattr(cfg, "get_auto_rag_enabled", lambda: True)
    monkeypatch.setattr(cfg, "get_auto_rag_top_k_pre", lambda: 20)
    monkeypatch.setattr(cfg, "get_auto_rag_top_k_post", lambda: 20)
    monkeypatch.setattr(cfg, "get_auto_rag_min_rerank_score", lambda: 0.0)
    monkeypatch.setattr(cfg, "get_auto_rag_min_bi_encoder_score", lambda: 0.55)
    monkeypatch.setattr(cfg, "get_auto_rag_max_tokens", lambda: 512)
    monkeypatch.setattr(cfg, "get_embedder", lambda: MagicMock(embed=lambda _q: [0.0]))
    monkeypatch.setattr(
        cfg,
        "get_vector_store",
        lambda: MagicMock(search=lambda **kw: list(raw)),
    )
    monkeypatch.setattr(cfg, "get_reranker", lambda: None)
    hits = retrieve_document_context("q", "u1", max_tokens=40)
    from services.context_budget import estimate_tokens

    assert hits
    assert sum(estimate_tokens(h.chunk_text) for h in hits) <= 40
    assert len(hits) < len(raw)


def test_search_files_skips_point_ids_in_shared_set(monkeypatch) -> None:
    def fake_semantic_search(_q, limit=5, user_id="default", scope_filter=None):
        del scope_filter
        return [
            SearchResult(
                file_path="/a",
                score=0.9,
                chunk_text="dup",
                point_id="p1",
            ),
            SearchResult(
                file_path="/b",
                score=0.8,
                chunk_text="keep",
                point_id=None,
            ),
        ]

    monkeypatch.setattr(
        "services.search.semantic_search",
        fake_semantic_search,
    )
    payload = tools_mod._search_files(
        {"query": "x"},
        user_id="u",
        auto_rag_point_ids={"p1"},
    )
    data = json.loads(payload)
    assert data["count"] == 1
    assert data["results"][0]["path"] == "/b"


def test_auto_rag_uses_visible_qdrant_filter(monkeypatch) -> None:
    import config as cfg

    captured: list[dict] = []

    class _VS:
        def search(self, **kwargs):
            captured.append(kwargs.get("filter") or {})
            return []

    monkeypatch.setattr(cfg, "get_auto_rag_enabled", lambda: True)
    monkeypatch.setattr(cfg, "get_auto_rag_top_k_pre", lambda: 5)
    monkeypatch.setattr(cfg, "get_auto_rag_top_k_post", lambda: 3)
    monkeypatch.setattr(cfg, "get_auto_rag_min_rerank_score", lambda: 0.0)
    monkeypatch.setattr(cfg, "get_auto_rag_min_bi_encoder_score", lambda: 0.55)
    monkeypatch.setattr(cfg, "get_auto_rag_max_tokens", lambda: 512)
    monkeypatch.setattr(cfg, "get_embedder", lambda: MagicMock(embed=lambda _q: [0.0]))
    monkeypatch.setattr(cfg, "get_vector_store", _VS)
    monkeypatch.setattr(cfg, "get_reranker", lambda: None)

    retrieve_document_context("q", "alice")
    retrieve_document_context("q", "bob")
    assert captured[0] == visible_qdrant_filter(UserContext(user_id="alice"))
    assert captured[1] == visible_qdrant_filter(UserContext(user_id="bob"))


def test_metadata_allowlist_drops_user_id(monkeypatch) -> None:
    import config as cfg

    raw = [
        {
            "id": "z",
            "score": 0.9,
            "payload": {
                "text": "body",
                "file_path": "/z",
                "scope": "personal",
                "user_id": "secret-user",
                "ingested": "2020-01-01T00:00:00Z",
            },
            "score_space": "cosine",
        },
    ]
    monkeypatch.setattr(cfg, "get_auto_rag_enabled", lambda: True)
    monkeypatch.setattr(cfg, "get_auto_rag_top_k_pre", lambda: 20)
    monkeypatch.setattr(cfg, "get_auto_rag_top_k_post", lambda: 3)
    monkeypatch.setattr(cfg, "get_auto_rag_min_rerank_score", lambda: 0.0)
    monkeypatch.setattr(cfg, "get_auto_rag_min_bi_encoder_score", lambda: 0.1)
    monkeypatch.setattr(cfg, "get_auto_rag_max_tokens", lambda: 512)
    monkeypatch.setattr(cfg, "get_embedder", lambda: MagicMock(embed=lambda _q: [0.0]))
    monkeypatch.setattr(
        cfg,
        "get_vector_store",
        lambda: MagicMock(search=lambda **kw: list(raw)),
    )
    monkeypatch.setattr(cfg, "get_reranker", lambda: None)
    hits = retrieve_document_context("q", "u1")
    assert hits[0].metadata.get("user_id") is None
    assert "secret-user" not in str(hits[0].metadata)


def test_auto_rag_never_raises(monkeypatch) -> None:
    import config as cfg

    monkeypatch.setattr(cfg, "get_auto_rag_enabled", lambda: True)
    monkeypatch.setattr(cfg, "get_auto_rag_top_k_pre", lambda: 20)
    monkeypatch.setattr(cfg, "get_auto_rag_top_k_post", lambda: 3)
    monkeypatch.setattr(cfg, "get_auto_rag_min_rerank_score", lambda: 0.0)
    monkeypatch.setattr(cfg, "get_auto_rag_min_bi_encoder_score", lambda: 0.55)
    monkeypatch.setattr(cfg, "get_auto_rag_max_tokens", lambda: 512)

    def _boom(_q):
        raise RuntimeError("embedder down")

    monkeypatch.setattr(cfg, "get_embedder", lambda: MagicMock(embed=_boom))
    assert retrieve_document_context("q", "u1") == []
