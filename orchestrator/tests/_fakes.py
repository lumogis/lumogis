# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Standalone test doubles, importable without the heavy ``conftest`` chain.

``conftest.py`` imports the full app (``config`` → adapters → credentials …),
so a test that only needs an in-memory vector store should import from here
instead of ``tests.conftest`` — this keeps such tests runnable in minimal
environments (e.g. a ``--noconftest`` harness) while ``conftest`` re-exports
these names for back-compat. Pure stdlib only — no app imports.
"""

from __future__ import annotations


def _match_clause(payload: dict, clause: dict) -> bool:
    """Evaluate a single Qdrant filter clause against a payload dict.

    Supports the small subset Lumogis actually uses today:
      * ``{"key": k, "match": {"value": v}}`` — equality
      * ``{"key": k, "match": {"any": [...]}}`` — membership
      * Nested ``{"must": [...]}`` / ``{"should": [...]}`` blocks

    The real Qdrant filter language is much richer; mirroring just what
    ``visibility.visible_qdrant_filter`` and the per-user/scope routes
    actually emit keeps the mock honest without re-implementing Qdrant.
    """
    if "must" in clause or "should" in clause:
        return _matches_qdrant_filter(payload, clause)
    key = clause["key"]
    match = clause["match"]
    actual = payload.get(key)
    if "value" in match:
        return actual == match["value"]
    if "any" in match:
        return actual in match["any"]
    raise NotImplementedError(f"MockVectorStore: unsupported match shape {match!r}")


def _matches_qdrant_filter(payload: dict, flt: dict) -> bool:
    """Top-level filter eval: AND across ``must``, OR across ``should``."""
    if "must" in flt:
        if not all(_match_clause(payload, c) for c in flt["must"]):
            return False
    if "should" in flt:
        if not any(_match_clause(payload, c) for c in flt["should"]):
            return False
    return True


class MockVectorStore:
    def __init__(self):
        self._collections: dict[str, list] = {}

    def ping(self) -> bool:
        return True

    def create_collection(self, name: str, vector_size: int) -> None:
        self._collections[name] = []

    def ensure_payload_index(self, collection: str, field: str) -> None:
        """No-op — in-memory mock has no payload-index API."""

    def ensure_tenant_payload_index(self, collection: str, field: str) -> None:
        """No-op — in-memory mock has no tenant-index API."""

    def upsert(self, collection: str, id: str, vector: list[float], payload: dict) -> None:
        self._collections.setdefault(collection, []).append(
            {"id": id, "vector": vector, "payload": payload}
        )

    def search(
        self,
        collection: str,
        vector: list[float],
        limit: int,
        threshold: float,
        filter: dict | None = None,
        sparse_query: str | None = None,
    ) -> list[dict]:
        items = self._collections.get(collection, [])
        if filter:
            items = [i for i in items if _matches_qdrant_filter(i.get("payload", {}), filter)]
        return [{"id": i["id"], "score": 1.0, "payload": i["payload"]} for i in items[:limit]]

    def delete(self, collection: str, id: str) -> None:
        items = self._collections.get(collection, [])
        self._collections[collection] = [i for i in items if i["id"] != id]

    def delete_where(self, collection: str, filter: dict) -> None:
        items = self._collections.get(collection, [])
        self._collections[collection] = [
            i for i in items if not _matches_qdrant_filter(i.get("payload", {}), filter)
        ]

    def count(self, collection: str) -> int:
        return len(self._collections.get(collection, []))

    def count_where(self, collection: str, filter: dict) -> int:
        items = self._collections.get(collection, [])
        return sum(
            1 for i in items if _matches_qdrant_filter(i.get("payload", {}), filter)
        )
