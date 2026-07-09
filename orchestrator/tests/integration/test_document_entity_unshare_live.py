# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live owner/admin document unshare → entity teardown (LUM-602).

Unit tests in ``tests/premium/test_document_entity_cascade.py`` pin the refcount
planner; ``test_document_shared_entity_cascade_live.py`` exercises retraction by
calling ``retract_document_entities`` directly after a manual ``file_index`` delete.

This suite proves the **real teardown call sites**:

* **Owner path** — ``projection.unproject_file`` (route / share job / registry)
  resolves ``file_path`` from the deleted shared row and retracts doc-only entities.
* **Admin path** — ``admin_unshare(resource='files')`` routes through the same
  ``unproject_file`` choke point; user-shared entities survive via downgrade.

Skips when Postgres + Qdrant are unreachable. Gate: ``make compose-test-integration``.
"""

from __future__ import annotations

import uuid

import pytest
from auth import UserContext

import config
from services import admin_unshare as admin_unshare_svc
from services import document_entity_cascade as cascade
from services import projection

pytestmark = pytest.mark.integration

ENTITIES = "entities"
DOCUMENTS = "documents"
ADMIN = UserContext(user_id="lum602-admin", role="admin", is_authenticated=True)


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
        for col in (ENTITIES, DOCUMENTS):
            try:
                vs.count(col)
            except Exception:
                vs.create_collection(col, _vector_dim())
                vs.count(col)
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


def _seed_personal_document(ms, vs, owner: str, file_path: str, n_chunks: int = 1) -> int:
    row = ms.fetch_one(
        "INSERT INTO file_index (file_path, file_hash, file_type, chunk_count, user_id, scope) "
        "VALUES (%s, %s, '.md', %s, %s, 'personal') RETURNING id",
        (file_path, "hash-" + uuid.uuid4().hex, n_chunks, owner),
    )
    doc_id = int(row["id"])
    dim = _vector_dim()
    for i in range(n_chunks):
        vec = [0.0] * dim
        vec[i % dim] = 1.0
        vs.upsert(
            collection=DOCUMENTS,
            id=str(uuid.uuid4()),
            vector=vec,
            payload={
                "user_id": owner,
                "file_path": file_path,
                "chunk_index": i,
                "text": f"chunk {i}",
                "scope": "personal",
            },
        )
    return doc_id


def _share_document_file(ms, vs, owner: str, doc_id: int, file_path: str, n_chunks: int) -> None:
    actor = UserContext(user_id=owner, is_authenticated=True)
    projection.project_file_with_status(
        {
            "id": doc_id,
            "user_id": owner,
            "file_path": file_path,
            "file_hash": "h",
            "file_type": ".md",
            "chunk_count": n_chunks,
        },
        target_scope="shared",
        actor=actor,
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


def _shared_doc_chunks(vs, owner: str, doc_id: int) -> list:
    pts = vs.scroll_collection(DOCUMENTS, user_id=owner, with_vectors=False)
    return [
        p
        for p in pts
        if (p.get("payload") or {}).get("scope") == "shared"
        and (p.get("payload") or {}).get("published_from") == doc_id
    ]


def _cleanup(ms, vs, owner: str) -> None:
    for stmt, args in (
        ("DELETE FROM entity_relations WHERE user_id = %s", (owner,)),
        ("DELETE FROM file_index WHERE user_id = %s", (owner,)),
        ("DELETE FROM entities WHERE user_id = %s", (owner,)),
        ("DELETE FROM audit_log WHERE user_id = %s", (ADMIN.user_id,)),
    ):
        try:
            ms.execute(stmt, args)
        except Exception:
            pass
    try:
        vs.delete_where(DOCUMENTS, {"must": [{"key": "user_id", "match": {"value": owner}}]})
        vs.delete_where(ENTITIES, {"must": [{"key": "user_id", "match": {"value": owner}}]})
    except Exception:
        pass


def test_owner_unshare_document_tears_down_entities(live_stores):
    """Owner ``unproject_file`` retracts doc-cascade shared entities (LUM-602)."""
    ms, vs = live_stores
    owner = f"lum602-owner-{uuid.uuid4().hex[:8]}"
    file_path = f"/uploads/{owner}/doc-{uuid.uuid4().hex[:6]}.md"
    actor = UserContext(user_id=owner, is_authenticated=True)
    try:
        doc_id = _seed_personal_document(ms, vs, owner, file_path, n_chunks=1)
        _share_document_file(ms, vs, owner, doc_id, file_path, n_chunks=1)
        assert len(_shared_doc_chunks(vs, owner, doc_id)) == 1

        e1 = _seed_personal_entity(ms, owner, "OwnerUnshareA")
        e2 = _seed_personal_entity(ms, owner, "OwnerUnshareB")
        _relate_to_document(ms, owner, e1, file_path)
        _relate_to_document(ms, owner, e2, file_path)
        projected, failed = cascade.cascade_share_document_entities(
            src_file={"file_path": file_path, "user_id": owner}, actor=actor
        )
        assert (projected, failed) == (2, 0)
        for src in (e1, e2):
            assert _shared_entity_row(ms, src) is not None

        removed = projection.unproject_file(doc_id, target_scope="shared")
        assert removed == 1
        assert _shared_doc_chunks(vs, owner, doc_id) == []
        for src in (e1, e2):
            assert _shared_entity_row(ms, src) is None, f"shared entity {src} orphaned"
            assert not _shared_point_exists(vs, src)

        # Personal source row survives owner unshare.
        assert ms.fetch_one(
            "SELECT id FROM file_index WHERE id = %s AND scope = 'personal'", (doc_id,)
        )
    finally:
        _cleanup(ms, vs, owner)


def test_admin_unshare_document_preserves_user_shared_entity(live_stores):
    """Admin ``admin_unshare(files)`` downgrades ``multiple`` → ``user``, not delete (LUM-602)."""
    ms, vs = live_stores
    owner = f"lum602-owner-{uuid.uuid4().hex[:8]}"
    file_path = f"/uploads/{owner}/admin-{uuid.uuid4().hex[:6]}.md"
    actor = UserContext(user_id=owner, is_authenticated=True)
    try:
        doc_id = _seed_personal_document(ms, vs, owner, file_path, n_chunks=1)
        _share_document_file(ms, vs, owner, doc_id, file_path, n_chunks=1)

        e = _seed_personal_entity(ms, owner, "AdminDualShared")
        src = {"entity_id": e, "name": "AdminDualShared", "entity_type": "PERSON"}
        projection.project_entity(src, target_scope="shared", actor=actor)
        assert _shared_entity_row(ms, e)["share_origin"] == "user"

        _relate_to_document(ms, owner, e, file_path)
        cascade.cascade_share_document_entities(
            src_file={"file_path": file_path, "user_id": owner}, actor=actor
        )
        assert _shared_entity_row(ms, e)["share_origin"] == "multiple"

        result = admin_unshare_svc.admin_unshare(
            actor=ADMIN, resource="files", pk=str(doc_id)
        )
        assert result["unshared"] is True
        assert result["source_owner_id"] == owner
        assert _shared_doc_chunks(vs, owner, doc_id) == []

        row = _shared_entity_row(ms, e)
        assert row is not None, "user-shared entity deleted on admin document unshare"
        assert row["share_origin"] == "user"
        assert _shared_point_exists(vs, e)
    finally:
        _cleanup(ms, vs, owner)
