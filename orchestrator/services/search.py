# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Semantic search: embed query, Qdrant retrieval, BGE reranking."""

import logging
import os
from pathlib import Path
from typing import Optional

from auth import UserContext
from models.search import SearchResult
from visibility import visible_qdrant_filter

import config

_log = logging.getLogger(__name__)


def _user_filter(user_id: str, scope_filter: Optional[str] = None) -> dict:
    """Build the Qdrant filter for a personal-or-household read.

    Thin wrapper around :func:`visibility.visible_qdrant_filter` so the
    `_user_filter` callsites already in the codebase keep working without
    a signature break — the helper now returns the household union
    `(scope='personal' AND user_id=$me) OR scope IN ('shared','system')`.
    """
    return visible_qdrant_filter(UserContext(user_id=user_id), scope_filter)


def semantic_search(
    query: str,
    limit: int = 5,
    user_id: str = "default",
    *,
    scope_filter: Optional[str] = None,
) -> list[SearchResult]:
    """Semantic search across the household-visible document corpus.

    Visibility resolved via :func:`visibility.visible_qdrant_filter` —
    the household union is the default; ``scope_filter`` narrows.
    """
    embedder = config.get_embedder()
    vs = config.get_vector_store()
    reranker = config.get_reranker()

    query_vec = embedder.embed(query)
    raw = vs.search(
        collection="documents",
        vector=query_vec,
        limit=20,
        threshold=0.40,
        filter=_user_filter(user_id, scope_filter),
        sparse_query=query,
    )

    if not raw:
        _log.debug("No Qdrant results for '%s', trying filename fallback", query)
        return _fuzzy_to_results(fuzzy_filename_search(query), limit)

    if reranker is not None:
        candidates = []
        for r in raw:
            candidates.append(
                {
                    "text": r["payload"].get("text", ""),
                    "file_path": r["payload"].get("file_path", ""),
                    "score": r["score"],
                    "payload": r["payload"],
                }
            )
        reranked = reranker.rerank(query, candidates, limit)
        return [
            SearchResult(
                file_path=c.get("file_path", c.get("payload", {}).get("file_path", "")),
                score=c.get("score", 0.0),
                chunk_text=c.get("text", ""),
                metadata=c.get("payload", {}),
            )
            for c in reranked
        ]

    return [
        SearchResult(
            file_path=r["payload"].get("file_path", ""),
            score=r["score"],
            chunk_text=r["payload"].get("text", ""),
            metadata=r["payload"],
        )
        for r in raw[:limit]
    ]


def fuzzy_filename_search(query: str, limit: int = 10) -> list[dict]:
    root_path = Path(os.environ.get("FILESYSTEM_ROOT", str(Path.home())))
    results = []
    if not root_path.is_dir():
        return results
    q = query.lower()
    for root, dirs, files in os.walk(root_path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if q in name.lower():
                full = Path(root) / name
                try:
                    size_kb = round(full.stat().st_size / 1024)
                except OSError:
                    size_kb = 0
                results.append({"path": str(full), "name": name, "size_kb": size_kb})
                if len(results) >= limit:
                    return results
    return results


def _fuzzy_to_results(hits: list[dict], limit: int) -> list[SearchResult]:
    return [
        SearchResult(
            file_path=h["path"],
            score=0.0,
            chunk_text=f"File: {h['name']} ({h['size_kb']} KB)",
        )
        for h in hits[:limit]
    ]
