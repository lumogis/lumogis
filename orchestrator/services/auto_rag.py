# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Chat auto-RAG: retrieve document chunks for context injection (LUM-308)."""

from __future__ import annotations

import logging
from typing import Literal

from auth import UserContext
from models.memory import DocumentContextHit
from services.context_budget import estimate_tokens
from visibility import visible_qdrant_filter

import config

_log = logging.getLogger(__name__)

_META_ALLOW = frozenset({"file_path", "scope", "ingested"})


def _merge_file_path_filter(base_filter: dict, file_path: str) -> dict:
    """AND-merge file_path narrowing under a parent must (visibility.py contract)."""
    return {
        "must": [
            base_filter,
            {"key": "file_path", "match": {"value": file_path}},
        ]
    }


def _parse_chunk_index(pl: dict) -> int | None:
    idx = pl.get("chunk_index")
    if idx is None:
        idx = pl.get("idx")
    if isinstance(idx, int):
        return idx
    if isinstance(idx, float):
        return int(idx)
    if isinstance(idx, str) and idx.isdigit():
        return int(idx)
    return None


def _project_metadata(payload: dict) -> dict:
    out: dict = {}
    for k in _META_ALLOW:
        if k in payload and payload[k] is not None:
            out[k] = payload[k]
    return out


def _score_space(raw_row: dict) -> Literal["rrf", "cosine"]:
    v = raw_row.get("score_space")
    if v == "rrf":
        return "rrf"
    return "cosine"


def _apply_max_tokens(hits: list[DocumentContextHit], max_tokens: int) -> list[DocumentContextHit]:
    if max_tokens <= 0 or not hits:
        return []
    ordered = sorted(hits, key=lambda h: h.score, reverse=True)
    kept = list(ordered)
    while kept and sum(estimate_tokens(x.chunk_text) for x in kept) > max_tokens:
        kept.pop()
    return kept


def retrieve_document_context(
    query: str,
    user_id: str,
    *,
    scope_filter: str | None = None,
    max_tokens: int | None = None,
    file_path: str | None = None,
    scoped: bool = False,
) -> list[DocumentContextHit]:
    """Retrieve gated document chunks for chat injection.

    Non-scoped path never raises (LUM-308). Scoped path propagates infra failures.
    """
    if not scoped and not file_path:
        if not config.get_auto_rag_enabled():
            return []
    try:
        return _retrieve_document_context_inner(
            query,
            user_id,
            scope_filter=scope_filter,
            max_tokens=max_tokens,
            file_path=file_path,
            scoped=scoped,
        )
    except Exception:
        if scoped:
            raise
        _log.warning(
            "auto_rag retrieval failed",
            extra={"event": "auto_rag_failed", "user_id": user_id},
            exc_info=True,
        )
        return []


def _retrieve_document_context_inner(
    query: str,
    user_id: str,
    *,
    scope_filter: str | None,
    max_tokens: int | None,
    file_path: str | None,
    scoped: bool,
) -> list[DocumentContextHit]:
    embedder = config.get_embedder()
    vs = config.get_vector_store()
    reranker = config.get_reranker()
    filt = visible_qdrant_filter(UserContext(user_id=user_id), scope_filter)
    if file_path:
        filt = _merge_file_path_filter(filt, file_path)

    if scoped:
        top_pre = config.get_document_chat_top_k_pre()
        top_post = config.get_document_chat_top_k_post()
        dense_threshold = 0.30
        min_bi = 0.45
    else:
        top_pre = config.get_auto_rag_top_k_pre()
        top_post = config.get_auto_rag_top_k_post()
        dense_threshold = 0.40
        min_bi = config.get_auto_rag_min_bi_encoder_score()

    min_rerank = config.get_auto_rag_min_rerank_score()

    query_vec = embedder.embed(query)
    raw = vs.search(
        collection="documents",
        vector=query_vec,
        limit=top_pre,
        threshold=dense_threshold,
        filter=filt,
        sparse_query=query,
    )
    if not raw:
        return []

    def _raw_to_candidates(rows: list[dict]) -> list[dict]:
        out = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "text": r["payload"].get("text", ""),
                    "file_path": r["payload"].get("file_path", ""),
                    "score": r["score"],
                    "payload": r["payload"],
                    "score_space": _score_space(r),
                }
            )
        return out

    hits: list[DocumentContextHit] = []

    if reranker is not None:
        try:
            candidates = _raw_to_candidates(raw)
            reranked = reranker.rerank(query, candidates, limit=len(candidates))
            accepted = [c for c in reranked if float(c.get("rerank_score") or 0.0) >= min_rerank][
                :top_post
            ]
            for c in accepted:
                pl = c.get("payload", {}) or {}
                scope_val = str(pl.get("scope") or "personal")
                if scope_val not in ("personal", "shared", "system"):
                    scope_val = "personal"
                ingested = pl.get("ingested")
                ingested_s = str(ingested) if ingested is not None else None
                chunk_index = _parse_chunk_index(pl)
                hits.append(
                    DocumentContextHit(
                        point_id=str(c["id"]),
                        file_path=str(c.get("file_path") or pl.get("file_path") or ""),
                        chunk_text=str(c.get("text") or ""),
                        score=float(c.get("rerank_score") or 0.0),
                        score_kind="rerank",
                        rerank_score=float(c.get("rerank_score") or 0.0),
                        scope=scope_val,
                        ingested=ingested_s,
                        metadata=_project_metadata(pl),
                        chunk_index=chunk_index,
                    )
                )
        except Exception:
            _log.warning(
                "auto_rag reranker failed; falling back to threshold path",
                extra={"event": "auto_rag_rerank_failed", "user_id": user_id},
                exc_info=True,
            )
            hits = _hits_without_rerank(raw, top_post, min_bi)

    else:
        hits = _hits_without_rerank(raw, top_post, min_bi)

    cap = max_tokens if max_tokens is not None else config.get_auto_rag_max_tokens()
    hits = _apply_max_tokens(hits, cap)
    return hits


def _hits_without_rerank(
    raw: list[dict],
    top_post: int,
    min_bi: float,
) -> list[DocumentContextHit]:
    """Order by score desc, apply bi-encoder floor only for dense cosine rows."""
    rows = sorted(raw, key=lambda r: float(r.get("score") or 0.0), reverse=True)
    picked: list[DocumentContextHit] = []
    for r in rows:
        if len(picked) >= top_post:
            break
        space = _score_space(r)
        score = float(r.get("score") or 0.0)
        if space == "cosine" and score < min_bi:
            continue
        pl = r.get("payload") or {}
        scope_val = str(pl.get("scope") or "personal")
        if scope_val not in ("personal", "shared", "system"):
            scope_val = "personal"
        ingested = pl.get("ingested")
        ingested_s = str(ingested) if ingested is not None else None
        chunk_index = _parse_chunk_index(pl)
        kind: Literal["bi_encoder", "rrf_gated"] = "rrf_gated" if space == "rrf" else "bi_encoder"
        picked.append(
            DocumentContextHit(
                point_id=str(r["id"]),
                file_path=str(pl.get("file_path", "")),
                chunk_text=str(pl.get("text", "")),
                score=score,
                score_kind=kind,
                rerank_score=None,
                scope=scope_val,
                ingested=ingested_s,
                metadata=_project_metadata(pl),
                chunk_index=chunk_index,
            )
        )
    return picked
