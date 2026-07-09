# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Reranker adapter using BGE-reranker-base via sentence-transformers CrossEncoder."""

import logging
import os
import sys

_log = logging.getLogger(__name__)


def _resolve_device(device: str | None) -> str | None:
    """Pick the CrossEncoder device.

    Explicit arg or ``RERANKER_DEVICE`` wins. Otherwise force CPU on macOS: the
    default device selection there is MPS (Apple GPU), which OOMs on the bundled
    Server (CPU-only appliance, no CUDA). Leave selection to sentence-transformers
    elsewhere so a CUDA Docker Core still uses the GPU (returns None = auto).
    """
    device = device or os.environ.get("RERANKER_DEVICE")
    if device is None and sys.platform == "darwin":
        device = "cpu"
    return device


class BGEReranker:
    def __init__(
        self, model_name: str = "BAAI/bge-reranker-base", device: str | None = None
    ) -> None:
        # Lazy import: keeps the heavy sentence-transformers/torch stack off the module import
        # path (only paid when a BGE reranker is actually constructed).
        from sentence_transformers import CrossEncoder

        device = _resolve_device(device)
        _log.info("Loading reranker model: %s (device=%s)", model_name, device or "auto")
        self._model = CrossEncoder(model_name, **({"device": device} if device else {}))
        _log.info("Reranker model loaded")

    def rerank(self, query: str, candidates: list[dict], limit: int) -> list[dict]:
        if not candidates:
            return []
        pairs = [(query, c.get("text", c.get("payload", {}).get("text", ""))) for c in candidates]
        scores = self._model.predict(pairs)
        for c, s in zip(candidates, scores):
            c["rerank_score"] = float(s)
        scored = sorted(candidates, key=lambda c: float(c["rerank_score"]), reverse=True)
        return scored[:limit]

    def warmup(self) -> None:
        """Force model load and dummy rerank to catch errors at startup."""
        self.rerank("test query", [{"text": "test document"}], limit=1)
        _log.info("Reranker warmup complete")
