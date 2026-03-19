# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""Embedder adapter for Ollama (Nomic Embed)."""

import logging

import httpx

_log = logging.getLogger(__name__)


class OllamaEmbedder:
    def __init__(self, url: str, model: str) -> None:
        self._url = url.rstrip("/")
        self._model = model
        self._vector_size: int | None = None

    def ping(self) -> bool:
        try:
            r = httpx.get(f"{self._url}/api/tags", timeout=5.0)
            return r.status_code == 200
        except Exception:
            return False

    @property
    def vector_size(self) -> int:
        if self._vector_size is None:
            test = self.embed("dimension probe")
            self._vector_size = len(test)
        return self._vector_size

    def embed(self, text: str) -> list[float]:
        r = httpx.post(
            f"{self._url}/api/embed",
            json={"model": self._model, "input": text},
            timeout=30.0,
        )
        r.raise_for_status()
        data = r.json()
        return data["embeddings"][0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        r = httpx.post(
            f"{self._url}/api/embed",
            json={"model": self._model, "input": texts},
            timeout=60.0,
        )
        r.raise_for_status()
        data = r.json()
        return data["embeddings"]
