"""VectorStore adapter for Qdrant."""

import logging

from qdrant_client import QdrantClient
from qdrant_client.models import Distance
from qdrant_client.models import PointStruct
from qdrant_client.models import VectorParams

_log = logging.getLogger(__name__)


class QdrantStore:
    def __init__(self, url: str) -> None:
        self._client = QdrantClient(url=url)

    def ping(self) -> bool:
        try:
            self._client.get_collections()
            return True
        except Exception:
            return False

    def create_collection(self, name: str, vector_size: int) -> None:
        existing = {c.name for c in self._client.get_collections().collections}
        if name in existing:
            _log.info("Collection '%s' already exists, skipping creation", name)
            return
        self._client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
        _log.info("Created Qdrant collection '%s' (dim=%d)", name, vector_size)

    def upsert(self, collection: str, id: str, vector: list[float], payload: dict) -> None:
        self._client.upsert(
            collection_name=collection,
            points=[PointStruct(id=id, vector=vector, payload=payload)],
        )

    def search(
        self, collection: str, vector: list[float], limit: int, threshold: float
    ) -> list[dict]:
        results = self._client.query_points(
            collection_name=collection,
            query=vector,
            limit=limit,
            score_threshold=threshold,
        )
        return [
            {"id": str(r.id), "score": r.score, "payload": r.payload}
            for r in results.points
        ]

    def delete(self, collection: str, id: str) -> None:
        self._client.delete(
            collection_name=collection,
            points_selector=[id],
        )

    def count(self, collection: str) -> int:
        info = self._client.get_collection(collection_name=collection)
        return info.points_count or 0
