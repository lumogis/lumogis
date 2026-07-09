# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live re-ingest removed-entity diff retraction (LUM-604).

Proves that ``reproject_shared_on_reingest`` tears down doc-origin shared
projections for entities dropped by a re-ingest, without waiting for unshare or
purge. Complements ``test_document_shared_entity_cascade_live.py`` (LUM-586).
"""

from __future__ import annotations

import uuid

import pytest
from auth import UserContext

import config
from services import document_entity_cascade as cascade
from services import projection

pytestmark = pytest.mark.integration

ENTITIES = "entities"


def _vector_dim() -> int:
    try:
        return int(config.get_embedder().vector_size)
    except Exception:
        return 768


@pytest.fixture
def live_stores(monkeypatch):
    import os

    try:
        from adapters.postgres_store import PostgresStore
        from adapters.qdrant_store import QdrantStore

        vs = QdrantStore(url=os.environ.get("QDRANT_URL", "http://qdrant:6333"))
        ms = PostgresStore(
            host=os.environ.get("POSTGRES_HOST", "postgres"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            user=os.environ.get("POSTGRES_USER", "lumogis"),
            password=os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
            dbname=os.environ.get("POSTGRES_DB", "lumogis"),
        )
        if not ms.ping():
            raise RuntimeError("postgres ping failed")
        try:
            vs.count(ENTITIES)
        except Exception:
            vs.create_collection(ENTITIES, _vector_dim())
            vs.count(ENTITIES)
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"real stack not reachable: {exc}")

    monkeypatch.setitem(config._instances, "vector_store", vs)
    monkeypatch.setitem(config._instances, "metadata_store", ms)
    monkeypatch.setattr(config, "get_graph_mode", lambda: "service")
    return ms, vs


def _seed_personal_entity(ms, owner: str, name: str) -> str:
    row = ms.fetch_one(
        "INSERT INTO entities (name, entity_type, user_id, scope) "
        "VALUES (%s, 'PERSON', %s, 'personal') RETURNING entity_id",
        (name, owner),
    )
    return str(row["entity_id"])


def _relate_to_document(ms, owner: str, entity_id: str, file_path: str) -> None:
    ms.execute(
        "INSERT INTO entity_relations "
        "(source_id, relation_type, evidence_type, evidence_id, user_id) "
        "VALUES (%s, 'MENTIONED_IN_DOCUMENT', 'DOCUMENT', %s, %s) "
        "ON CONFLICT DO NOTHING",
        (entity_id, file_path, owner),
    )


def _seed_personal_file_index(ms, owner: str, file_path: str) -> int:
    row = ms.fetch_one(
        "INSERT INTO file_index "
        "(file_path, file_hash, file_type, chunk_count, user_id, scope) "
        "VALUES (%s, %s, '.md', 1, %s, 'personal') RETURNING id",
        (file_path, "h-" + uuid.uuid4().hex, owner),
    )
    return int(row["id"])


def _seed_shared_file_index(ms, owner: str, file_path: str, published_from: int) -> None:
    ms.execute(
        "INSERT INTO file_index "
        "(file_path, file_hash, file_type, chunk_count, user_id, scope, published_from) "
        "VALUES (%s, %s, '.md', 1, %s, 'shared', %s)",
        (file_path, "h-" + uuid.uuid4().hex, owner, published_from),
    )


def _shared_entity_row(ms, src_entity_id: str) -> dict | None:
    return ms.fetch_one(
        "SELECT entity_id, share_origin FROM entities "
        "WHERE scope = 'shared' AND published_from = %s",
        (src_entity_id,),
    )


def _shared_point_exists(vs, src_entity_id: str) -> bool:
    pid = projection.projection_point_id(ENTITIES, src_entity_id, "shared")
    try:
        return bool(vs.retrieve(ENTITIES, [pid]))
    except Exception:
        pts = vs.scroll_collection(ENTITIES, with_vectors=False)
        return any(str(p.get("id")) == pid for p in pts)


def _cleanup(ms, vs, owner: str) -> None:
    for stmt, args in (
        ("DELETE FROM entity_relations WHERE user_id = %s", (owner,)),
        ("DELETE FROM file_index WHERE user_id = %s", (owner,)),
        ("DELETE FROM entities WHERE user_id = %s", (owner,)),
    ):
        try:
            ms.execute(stmt, args)
        except Exception:
            pass
    try:
        vs.delete_where(ENTITIES, {"must": [{"key": "user_id", "match": {"value": owner}}]})
    except Exception:
        pass


def test_reingest_retracts_dropped_document_entity(live_stores):
    ms, vs = live_stores
    owner = f"itest-{uuid.uuid4().hex[:8]}"
    file_path = f"/uploads/{owner}/reingest-{uuid.uuid4().hex[:6]}.md"
    actor = UserContext(user_id=owner, is_authenticated=True)
    try:
        keep = _seed_personal_entity(ms, owner, "KeepMe")
        drop = _seed_personal_entity(ms, owner, "DropMe")
        _relate_to_document(ms, owner, keep, file_path)
        _relate_to_document(ms, owner, drop, file_path)
        pk = _seed_personal_file_index(ms, owner, file_path)
        _seed_shared_file_index(ms, owner, file_path, pk)

        cascade.cascade_share_document_entities(
            src_file={"file_path": file_path, "user_id": owner}, actor=actor
        )
        assert _shared_entity_row(ms, keep) is not None
        assert _shared_entity_row(ms, drop) is not None

        removed = cascade.prune_stale_document_entity_relations(ms, file_path, owner, [keep])
        assert removed == [drop]

        projection.reproject_shared_on_reingest(owner, file_path, removed_entity_ids=removed)

        assert _shared_entity_row(ms, keep) is not None
        assert _shared_point_exists(vs, keep)
        assert _shared_entity_row(ms, drop) is None
        assert not _shared_point_exists(vs, drop)
    finally:
        _cleanup(ms, vs, owner)


def test_reingest_keeps_entity_justified_by_other_shared_doc(live_stores):
    ms, vs = live_stores
    owner = f"itest-{uuid.uuid4().hex[:8]}"
    doc_a = f"/uploads/{owner}/a-{uuid.uuid4().hex[:6]}.md"
    doc_b = f"/uploads/{owner}/b-{uuid.uuid4().hex[:6]}.md"
    actor = UserContext(user_id=owner, is_authenticated=True)
    try:
        e = _seed_personal_entity(ms, owner, "BothDocs")
        pk_a = _seed_personal_file_index(ms, owner, doc_a)
        pk_b = _seed_personal_file_index(ms, owner, doc_b)
        _relate_to_document(ms, owner, e, doc_a)
        _relate_to_document(ms, owner, e, doc_b)
        _seed_shared_file_index(ms, owner, doc_a, pk_a)
        _seed_shared_file_index(ms, owner, doc_b, pk_b)

        cascade.cascade_share_document_entities(
            src_file={"file_path": doc_a, "user_id": owner}, actor=actor
        )
        assert _shared_entity_row(ms, e) is not None

        removed = cascade.prune_stale_document_entity_relations(ms, doc_a, owner, [])
        assert removed == [e]

        projection.reproject_shared_on_reingest(owner, doc_a, removed_entity_ids=removed)

        assert _shared_entity_row(ms, e) is not None, (
            "entity wrongly retracted while doc B still justifies it"
        )
        assert _shared_point_exists(vs, e)
    finally:
        _cleanup(ms, vs, owner)
