# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""VectorStore adapter for Qdrant.

Hybrid search: the ``documents`` collection stores both a dense vector (cosine
similarity) and a BM25 sparse vector.  When ``sparse_query`` is supplied to
``search()``, both are queried in parallel and fused with Reciprocal Rank
Fusion (RRF).  All other collections fall back to dense-only search.

Community adapters (Chroma, Milvus, …) must add the ``sparse_query``
parameter to their ``search()`` signature to satisfy the VectorStore Protocol,
but may ignore it if the backend does not support sparse vectors.
"""

import hashlib
import logging
import re

from qdrant_client import QdrantClient
from qdrant_client.models import Distance
from qdrant_client.models import FieldCondition
from qdrant_client.models import Filter
from qdrant_client.models import Fusion
from qdrant_client.models import FusionQuery
from qdrant_client.models import MatchValue
from qdrant_client.models import Modifier
from qdrant_client.models import PointStruct
from qdrant_client.models import Prefetch
from qdrant_client.models import SparseIndexParams
from qdrant_client.models import SparseVector
from qdrant_client.models import SparseVectorParams
from qdrant_client.models import VectorParams

_log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"\b\w+\b")

# The documents collection uses BM25 sparse vectors for hybrid search.
_SPARSE_COLLECTION = "documents"
_SPARSE_VECTOR_NAME = "bm25"


def _tok_hash(token: str) -> int:
    """Stable 20-bit hash for a lowercase token (hashlib, not Python hash())."""
    return int.from_bytes(hashlib.md5(token.encode()).digest()[:4], "big") % (2**20)


def _tokenize_bm25(text: str) -> tuple[list[int], list[float]]:
    """Sparse BM25 representation: (indices, raw_term_counts).

    Qdrant's ``Modifier.IDF`` applies corpus-level IDF weighting at query time,
    so we only need to supply raw term frequencies here.
    """
    tokens = _TOKEN_RE.findall(text.lower())
    if not tokens:
        return [], []
    counts: dict[int, float] = {}
    for tok in tokens:
        h = _tok_hash(tok)
        counts[h] = counts.get(h, 0) + 1.0
    return list(counts.keys()), list(counts.values())


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
            if name == _SPARSE_COLLECTION:
                self._ensure_sparse_config(name)
            return

        sparse_cfg = (
            {
                _SPARSE_VECTOR_NAME: SparseVectorParams(
                    index=SparseIndexParams(on_disk=False),
                    modifier=Modifier.IDF,
                )
            }
            if name == _SPARSE_COLLECTION
            else None
        )
        self._client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            sparse_vectors_config=sparse_cfg,
        )
        _log.info(
            "Created Qdrant collection '%s' (dim=%d, sparse=%s)",
            name,
            vector_size,
            sparse_cfg is not None,
        )

    def _ensure_sparse_config(self, collection_name: str) -> None:
        """Add BM25 sparse vector config to an existing collection if absent.

        Called when ``documents`` collection already exists (e.g. upgrade from a
        version before hybrid search).  Safe to call repeatedly.
        """
        try:
            info = self._client.get_collection(collection_name)
            sparse = info.config.params.sparse_vectors or {}
            if _SPARSE_VECTOR_NAME not in sparse:
                self._client.update_collection(
                    collection_name=collection_name,
                    sparse_vectors_config={
                        _SPARSE_VECTOR_NAME: SparseVectorParams(
                            index=SparseIndexParams(on_disk=False),
                            modifier=Modifier.IDF,
                        )
                    },
                )
                _log.info(
                    "Upgraded '%s' collection with BM25 sparse vector config", collection_name
                )
        except Exception:
            _log.exception(
                "Failed to ensure sparse config for '%s' — hybrid search will be unavailable",
                collection_name,
            )

    def upsert(self, collection: str, id: str, vector: list[float], payload: dict) -> None:
        if collection == _SPARSE_COLLECTION:
            text = payload.get("text", "")
            indices, values = _tokenize_bm25(text) if text else ([], [])
            if indices:
                try:
                    # Named-vector dict: "" = default unnamed dense, "bm25" = sparse.
                    self._client.upsert(
                        collection_name=collection,
                        points=[
                            PointStruct(
                                id=id,
                                vector={
                                    "": vector,
                                    _SPARSE_VECTOR_NAME: SparseVector(
                                        indices=indices, values=values
                                    ),
                                },
                                payload=payload,
                            )
                        ],
                    )
                    return
                except Exception:
                    _log.debug(
                        "Hybrid upsert rejected by Qdrant for '%s' (sparse config missing?); "
                        "falling back to dense-only. Re-create the collection to enable BM25.",
                        collection,
                    )

        self._client.upsert(
            collection_name=collection,
            points=[PointStruct(id=id, vector=vector, payload=payload)],
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
        query_filter = self._build_filter(filter) if filter else None

        if sparse_query and collection == _SPARSE_COLLECTION:
            sparse_idx, sparse_vals = _tokenize_bm25(sparse_query)
            if sparse_idx:
                try:
                    results = self._client.query_points(
                        collection_name=collection,
                        prefetch=[
                            Prefetch(
                                query=vector,
                                limit=limit * 4,
                                score_threshold=threshold,
                                filter=query_filter,
                            ),
                            Prefetch(
                                query=SparseVector(indices=sparse_idx, values=sparse_vals),
                                using=_SPARSE_VECTOR_NAME,
                                limit=limit * 4,
                                filter=query_filter,
                            ),
                        ],
                        query=FusionQuery(fusion=Fusion.RRF),
                        limit=limit,
                        with_payload=True,
                    )
                    return [
                        {"id": str(r.id), "score": r.score, "payload": r.payload}
                        for r in results.points
                    ]
                except Exception:
                    _log.debug(
                        "Hybrid search rejected by Qdrant for '%s' (sparse config missing?); "
                        "falling back to dense-only.",
                        collection,
                    )

        results = self._client.query_points(
            collection_name=collection,
            query=vector,
            limit=limit,
            score_threshold=threshold,
            query_filter=query_filter,
        )
        return [{"id": str(r.id), "score": r.score, "payload": r.payload} for r in results.points]

    def delete(self, collection: str, id: str) -> None:
        self._client.delete(
            collection_name=collection,
            points_selector=[id],
        )

    def count(self, collection: str) -> int:
        info = self._client.get_collection(collection_name=collection)
        return info.points_count or 0

    def scroll_collection(
        self,
        name: str,
        user_id: str | None = None,
        with_vectors: bool = False,
        batch_size: int = 100,
    ) -> list[dict]:
        """Return all points in *name* as plain dicts.

        Used by backup and export — not part of the VectorStore Protocol.
        """
        query_filter = None
        if user_id is not None:
            query_filter = Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            )

        points: list[dict] = []
        offset = None
        while True:
            batch, offset = self._client.scroll(
                collection_name=name,
                scroll_filter=query_filter,
                offset=offset,
                limit=batch_size,
                with_payload=True,
                with_vectors=with_vectors,
            )
            for pt in batch:
                vec = pt.vector if with_vectors else None
                points.append({"id": str(pt.id), "payload": pt.payload, "vector": vec})
            if offset is None:
                break
        return points

    @staticmethod
    def _build_filter(filter_dict: dict) -> Filter:
        """Translate portable filter dict to Qdrant Filter objects.

        Expected format: {"must": [{"key": "field", "match": {"value": "x"}}]}
        """
        conditions = []
        for clause in filter_dict.get("must", []):
            conditions.append(
                FieldCondition(
                    key=clause["key"],
                    match=MatchValue(value=clause["match"]["value"]),
                )
            )
        return Filter(must=conditions)
