# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The default-user remap re-tags Qdrant points too (LUM-997 rc.1 smoke).

Without it an updating household's library disappeared from search after the
setup claim: Postgres rows moved to the new admin, but every vector kept
``user_id='default'`` and search shows personal points only to their owner.
Runs against qdrant_client's in-memory engine, so the filter and set_payload
behaviour is real.
"""

from __future__ import annotations

import db_default_user_remap as remap
import qdrant_client
from qdrant_client.models import Distance
from qdrant_client.models import PointStruct
from qdrant_client.models import VectorParams


def _client_with_points():
    client = qdrant_client.QdrantClient(":memory:")
    for name in ("documents", "conversations"):
        client.create_collection(
            name, vectors_config=VectorParams(size=2, distance=Distance.COSINE)
        )
    client.upsert(
        "documents",
        points=[
            PointStruct(
                id=1, vector=[1.0, 0.0], payload={"user_id": "default", "scope": "personal"}
            ),
            PointStruct(
                id=2, vector=[0.0, 1.0], payload={"user_id": "someone", "scope": "personal"}
            ),
        ],
    )
    client.upsert(
        "conversations",
        points=[PointStruct(id=3, vector=[1.0, 1.0], payload={"user_id": "default"})],
    )
    return client


def _owners(client, name):
    points, _ = client.scroll(name, with_payload=True)
    return {p.id: p.payload["user_id"] for p in points}


def test_remap_qdrant_retags_default_points_in_every_collection(monkeypatch):
    client = _client_with_points()
    monkeypatch.setattr(qdrant_client, "QdrantClient", lambda **_: client)
    monkeypatch.delenv("VECTOR_STORE_BACKEND", raising=False)

    assert remap._remap_qdrant("admin-1") == 2
    assert _owners(client, "documents") == {1: "admin-1", 2: "someone"}
    assert _owners(client, "conversations") == {3: "admin-1"}
    # Idempotent: a second run finds nothing.
    assert remap._remap_qdrant("admin-1") == 0


def test_remap_qdrant_unreachable_is_logged_not_raised(monkeypatch):
    def boom(**_):
        raise ConnectionError("qdrant down")

    monkeypatch.setattr(qdrant_client, "QdrantClient", boom)
    assert remap._remap_qdrant("admin-1") == -1


def test_remap_qdrant_skipped_for_other_backends(monkeypatch):
    monkeypatch.setenv("VECTOR_STORE_BACKEND", "other")
    assert remap._remap_qdrant("admin-1") == 0
