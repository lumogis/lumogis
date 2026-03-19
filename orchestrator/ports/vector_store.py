# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""Port: vector store protocol.

Implemented by adapters/qdrant_store.py (default).
Swap the backend by writing one new adapter and changing VECTOR_STORE_BACKEND in .env.

Collections used by core
-------------------------
  documents    — chunked document text embeddings (768-dim, Nomic Embed)
  conversations — session summary embeddings for memory retrieval
  entities     — entity name + context_tag embeddings for resolution
  signals      — signal content_summary embeddings for semantic deduplication

All collections use the same vector size (config.get_embedder().vector_size).

Search return format
--------------------
search() returns a list of dicts, each with at minimum:
  {"id": str, "score": float, "payload": dict}
where payload contains the metadata stored at upsert time.

Implementing a new backend
--------------------------
See CONTRIBUTING.md for a worked Chroma example. The sparse_query parameter
must be accepted even if the backend does not support sparse/hybrid retrieval —
implement it as a no-op. The filter parameter uses the payload filter structure
defined by the adapter (e.g. Qdrant filter dicts).
"""

from typing import Protocol


class VectorStore(Protocol):
    def ping(self) -> bool:
        """Return True if the vector store is reachable. Does not raise."""
        ...

    def create_collection(self, name: str, vector_size: int) -> None:
        """Create a collection if it does not already exist.

        Idempotent — calling on an existing collection must not raise.
        """
        ...

    def upsert(
        self,
        collection: str,
        id: str,
        vector: list[float],
        payload: dict,
    ) -> None:
        """Insert or update a vector with its metadata payload.

        id: stable string identifier (UUID recommended). Upserting with the
            same id overwrites the existing entry.
        payload: arbitrary metadata stored alongside the vector and returned
            in search results.
        """
        ...

    def search(
        self,
        collection: str,
        vector: list[float],
        limit: int,
        threshold: float,
        filter: dict | None = None,
        sparse_query: str | None = None,
    ) -> list[dict]:
        """Return nearest neighbours above the score threshold.

        threshold: minimum similarity score (0.0–1.0); results below are excluded.
        filter: optional backend-specific payload filter (e.g. {"user_id": "default"}).
        sparse_query: optional raw text for hybrid dense+sparse retrieval (BM25).
            Backends that do not support sparse search must accept and ignore this.

        Returns list of {"id": str, "score": float, "payload": dict}, highest score first.
        """
        ...

    def delete(self, collection: str, id: str) -> None:
        """Delete a single vector by ID. No-op if the ID does not exist."""
        ...

    def count(self, collection: str) -> int:
        """Return the number of vectors in the collection."""
        ...
