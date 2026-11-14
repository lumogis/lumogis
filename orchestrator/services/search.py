# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Semantic search: embed query, Qdrant retrieval, BGE reranking."""

import logging
from pathlib import Path
from typing import Optional

from auth import UserContext
from models.search import SearchResult
from visibility import visible_filter
from visibility import visible_qdrant_filter

import config

_log = logging.getLogger(__name__)


def _caller_context(user_id: str) -> UserContext:
    """Build the caller context the way search does, from a bare ``user_id``.

    Chat file tools (``read_file``, the filename fallback) use this too, so
    chat and search share one visibility definition (LUM-884).
    ``UserContext`` is not marked authenticated here, so ``allows_shared``
    is read from the database.
    """
    return UserContext(user_id=user_id)


def _user_filter(user_id: str, scope_filter: Optional[str] = None) -> dict:
    """Build the Qdrant filter for a personal-or-household read.

    Thin wrapper around :func:`visibility.visible_qdrant_filter` so the
    `_user_filter` callsites already in the codebase keep working without
    a signature break — the helper now returns the household union
    `(scope='personal' AND user_id=$me) OR scope IN ('shared','system')`.
    """
    return visible_qdrant_filter(_caller_context(user_id), scope_filter)


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
        # The old disk walk never touched Postgres; this fallback can fail during
        # a DB outage. Callers then see "no results" and the warning below is the
        # only signal (LUM-884).
        try:
            hits = fuzzy_filename_search(query, user_id=user_id, scope_filter=scope_filter)
        except Exception:
            _log.warning("filename fallback failed", exc_info=True)
            return []
        return _fuzzy_to_results(hits, limit)

    if reranker is not None:
        candidates = []
        for r in raw:
            candidates.append(
                {
                    "id": r["id"],
                    "text": r["payload"].get("text", ""),
                    "file_path": r["payload"].get("file_path", ""),
                    "score": r["score"],
                    "payload": r["payload"],
                    "score_space": r.get("score_space", "cosine"),
                }
            )
        reranked = reranker.rerank(query, candidates, limit)
        return [
            SearchResult(
                file_path=c.get("file_path", c.get("payload", {}).get("file_path", "")),
                score=c.get("score", 0.0),
                chunk_text=c.get("text", ""),
                metadata=c.get("payload", {}),
                point_id=str(c.get("id")) if c.get("id") is not None else None,
                rerank_score=c.get("rerank_score"),
            )
            for c in reranked
        ]

    return [
        SearchResult(
            file_path=r["payload"].get("file_path", ""),
            score=r["score"],
            chunk_text=r["payload"].get("text", ""),
            metadata=r["payload"],
            point_id=str(r["id"]) if r.get("id") is not None else None,
            rerank_score=None,
        )
        for r in raw[:limit]
    ]


def fuzzy_filename_search(
    query: str,
    limit: int = 10,
    *,
    user_id: str,
    scope_filter: Optional[str] = None,
) -> list[dict]:
    """Find indexed files by name, under the same visibility rule as search.

    Queries ``file_index`` through :func:`visibility.visible_filter` for the
    caller (LUM-884); it no longer walks the disk, so it cannot name another
    member's files. Raises on metadata-store errors; callers degrade.
    """
    q = (query or "").strip()
    if not q:
        return []
    clause, params = visible_filter(_caller_context(user_id), scope_filter)
    # "!" is the LIKE escape character, so no backslash literal is needed.
    escaped = q.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    pattern = "%" + escaped + "%"
    rows = config.get_metadata_store().fetch_all(
        "SELECT DISTINCT file_path FROM file_index "
        f"WHERE {clause} "
        "AND regexp_replace(file_path, '^.*/', '') ILIKE %s ESCAPE '!' "
        "ORDER BY file_path LIMIT %s",
        (*params, pattern, limit),
    )
    results: list[dict] = []
    for row in rows:
        file_path = row["file_path"]
        p = Path(file_path)
        try:
            size_kb = round(p.stat().st_size / 1024)
        except OSError:
            size_kb = 0
        results.append({"path": file_path, "name": p.name, "size_kb": size_kb})
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
