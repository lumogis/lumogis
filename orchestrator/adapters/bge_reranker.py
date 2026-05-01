# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Reranker adapter using BGE-reranker-base via sentence-transformers CrossEncoder."""

import logging

from sentence_transformers import CrossEncoder

_log = logging.getLogger(__name__)


class BGEReranker:
    def __init__(self, model_name: str = "BAAI/bge-reranker-base") -> None:
        _log.info("Loading reranker model: %s", model_name)
        self._model = CrossEncoder(model_name)
        _log.info("Reranker model loaded")

    def rerank(self, query: str, candidates: list[dict], limit: int) -> list[dict]:
        if not candidates:
            return []
        pairs = [(query, c.get("text", c.get("payload", {}).get("text", ""))) for c in candidates]
        scores = self._model.predict(pairs)
        scored = list(zip(candidates, scores))
        scored.sort(key=lambda x: float(x[1]), reverse=True)
        return [c for c, _ in scored[:limit]]

    def warmup(self) -> None:
        """Force model load and dummy rerank to catch errors at startup."""
        self.rerank("test query", [{"text": "test document"}], limit=1)
        _log.info("Reranker warmup complete")
