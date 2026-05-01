# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Port: reranker protocol.

Implemented by adapters/bge_reranker.py (default, uses Ollama).
The reranker is an optional step in the retrieval pipeline — if disabled,
search results are returned in raw cosine-similarity order.

When to use
-----------
Reranking re-scores a shortlist of vector search candidates using a
cross-encoder model (slow but accurate), then returns the top-k.
It is invoked by services/knowledge.py after a vector store search with
a larger limit (e.g. top-50 → rerank → top-5).

Disabling
---------
Set RERANKER_ENABLED=false in .env to skip reranking entirely. A NullReranker
adapter returns the candidates list unchanged in that case.

Candidates format
-----------------
Each candidate dict must contain at minimum a "text" key (the content to
score against the query). All other keys are passed through unchanged so
that payload fields (id, score, metadata) survive the reranking step.
"""

from typing import Protocol


class Reranker(Protocol):
    def rerank(self, query: str, candidates: list[dict], limit: int) -> list[dict]:
        """Score candidates against the query and return the top-limit results.

        query: the user's original search text
        candidates: list of dicts, each containing at least {"text": str, ...}
        limit: maximum number of results to return

        Returns a sublist of candidates (may include a "rerank_score" key added
        by the implementation), sorted by relevance descending.
        """
        ...
