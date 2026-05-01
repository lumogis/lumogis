# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Port: embedder protocol.

Implemented by adapters/nomic_embedder.py (default, uses Ollama).
The vector_size property must match the collection dimension in the vector store.

Changing the embedder
---------------------
If you swap backends, drop and recreate all vector store collections — existing
vectors are incompatible with a different model. This is a one-time operation.
Set EMBEDDER_MODEL in .env to select a different Ollama model (any model whose
output matches vector_size). ONNX or API-backed embedders can be added by
implementing this Protocol.

Batch embedding
---------------
Use embed_batch() when processing multiple texts (ingestion, bulk signal import).
It is more efficient than repeated embed() calls as most backends support
a native batch endpoint.
"""

from typing import Protocol


class Embedder(Protocol):
    def ping(self) -> bool:
        """Return True if the embedding service is reachable AND the configured model is available.

        For Ollama-based embedders: must verify both that the Ollama API is responding
        (/api/tags 200) AND that the specific embedding model is present (/api/show 200).
        For API-based embedders (e.g. OpenAI): True if the API endpoint is reachable
        and the API key is valid (liveness only — model availability assumed for cloud APIs).
        Does not raise. Returns False on any failure.
        """
        ...

    @property
    def vector_size(self) -> int:
        """Dimension of the embedding vectors produced by this model.

        Used at startup to create vector store collections with the correct size.
        Example: 768 for nomic-embed-text, 1536 for text-embedding-3-small.
        """
        ...

    def embed(self, text: str) -> list[float]:
        """Embed a single text string. Returns a float vector of length vector_size."""
        ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of strings. Returns one vector per input, preserving order."""
        ...
