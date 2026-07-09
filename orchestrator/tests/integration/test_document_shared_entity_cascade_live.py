# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live refcounted document→entity cascade + retraction (LUM-586).

The port-level unit tests (``tests/premium/test_document_entity_cascade.py``)
pin the *decision logic* with a fake metadata store. This suite runs the same
functions against a **real Postgres + Qdrant** (the compose ``orchestrator``
container's stack) so the SQL the refcount planner and retraction actually
issue — ``ON CONFLICT`` provenance promotion, the ``other_justification``
EXISTS sub-query, ``ANY(%s)`` deletes/downgrades, and the post-commit shared
Qdrant point sweep — is proven end to end. The live FalkorDB edge behaviour is
covered separately by the KG suite
(``services/lumogis-graph/tests/test_document_shared_graph_integration.py``).

Skips cleanly when the real stack is not reachable, so it is safe in a plain
host unit run and only exercises the real path under
``make compose-test`` / ``make compose-test-integration``.
"""

from __future__ import annotations

import uuid

import pytest
from auth import UserContext
from services.document_purge import purge_document

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
    """Wire the real Postgres + Qdrant (else skip), and force graph mode on.

    Runs after ``tests/conftest.py``'s autouse in-memory swap and overrides the
    two store instances with real adapters. ``get_graph_mode`` is forced to
    ``service`` so the cascade is not gated off; the outbound ``DOCUMENT_SHARED``
    fire has no registered listener here and is a harmless no-op (the KG graph
    projection is proven by the KG live suite).
    """
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


# ---------------------------------------------------------------------------
# Seed / inspect / cleanup helpers
# ---------------------------------------------------------------------------


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


def _seed_shared_file_index(ms, owner: str, file_path: str, published_from: int) -> None:
    ms.execute(
        "INSERT INTO file_index "
        "(file_path, file_hash, file_type, chunk_count, user_id, scope, published_from) "
        "VALUES (%s, %s, '.md', 1, %s, 'shared', %s)",
        (file_path, "h-" + uuid.uuid4().hex, owner, published_from),
    )


def _seed_personal_file_index(ms, owner: str, file_path: str) -> int:
    row = ms.fetch_one(
        "INSERT INTO file_index "
        "(file_path, file_hash, file_type, chunk_count, user_id, scope) "
        "VALUES (%s, %s, '.md', 1, %s, 'personal') RETURNING id",
        (file_path, "h-" + uuid.uuid4().hex, owner),
    )
    return int(row["id"])


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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cascade_projects_document_entities_with_document_origin(live_stores):
    ms, vs = live_stores
    owner = f"itest-{uuid.uuid4().hex[:8]}"
    file_path = f"/uploads/{owner}/doc-{uuid.uuid4().hex[:6]}.md"
    actor = UserContext(user_id=owner, is_authenticated=True)
    try:
        e1 = _seed_personal_entity(ms, owner, "Ada")
        e2 = _seed_personal_entity(ms, owner, "Babbage")
        _relate_to_document(ms, owner, e1, file_path)
        _relate_to_document(ms, owner, e2, file_path)

        projected, failed = cascade.cascade_share_document_entities(
            src_file={"file_path": file_path, "user_id": owner}, actor=actor
        )
        assert (projected, failed) == (2, 0)

        for src in (e1, e2):
            row = _shared_entity_row(ms, src)
            assert row is not None, f"shared projection missing for {src}"
            assert row["share_origin"] == "document"
            assert _shared_point_exists(vs, src), f"shared Qdrant point missing for {src}"
    finally:
        _cleanup(ms, vs, owner)


def test_refcount_survives_until_last_shared_doc(live_stores):
    ms, vs = live_stores
    owner = f"itest-{uuid.uuid4().hex[:8]}"
    doc_a = f"/uploads/{owner}/a-{uuid.uuid4().hex[:6]}.md"
    doc_b = f"/uploads/{owner}/b-{uuid.uuid4().hex[:6]}.md"
    actor = UserContext(user_id=owner, is_authenticated=True)
    try:
        e = _seed_personal_entity(ms, owner, "SharedPerson")
        pk_a = _seed_personal_file_index(ms, owner, doc_a)
        pk_b = _seed_personal_file_index(ms, owner, doc_b)
        _relate_to_document(ms, owner, e, doc_a)
        _relate_to_document(ms, owner, e, doc_b)

        # Both docs shared; the entity projects once (origin 'document').
        cascade.cascade_share_document_entities(
            src_file={"file_path": doc_a, "user_id": owner}, actor=actor
        )
        _seed_shared_file_index(ms, owner, doc_a, pk_a)
        _seed_shared_file_index(ms, owner, doc_b, pk_b)
        assert _shared_entity_row(ms, e) is not None

        # Unshare doc A (owner path deletes the shared file_index row, then retracts).
        ms.execute(
            "DELETE FROM file_index WHERE published_from = %s AND scope = 'shared'", (pk_a,)
        )
        cascade.retract_document_entities(file_path=doc_a, owner_user_id=owner)
        assert _shared_entity_row(ms, e) is not None, (
            "entity retracted while doc B still justifies it"
        )
        assert _shared_point_exists(vs, e)

        # Unshare doc B — now the last justification is gone → retract.
        ms.execute(
            "DELETE FROM file_index WHERE published_from = %s AND scope = 'shared'", (pk_b,)
        )
        cascade.retract_document_entities(file_path=doc_b, owner_user_id=owner)
        assert _shared_entity_row(ms, e) is None, "entity not retracted on last unshare"
        assert not _shared_point_exists(vs, e), "shared Qdrant point orphaned after retraction"
    finally:
        _cleanup(ms, vs, owner)


def test_user_shared_entity_survives_document_unshare_via_downgrade(live_stores):
    ms, vs = live_stores
    owner = f"itest-{uuid.uuid4().hex[:8]}"
    doc_a = f"/uploads/{owner}/a-{uuid.uuid4().hex[:6]}.md"
    actor = UserContext(user_id=owner, is_authenticated=True)
    try:
        e = _seed_personal_entity(ms, owner, "DualShared")
        src = {"entity_id": e, "name": "DualShared", "entity_type": "PERSON"}

        # Direct LUM-581 share first → origin 'user'.
        projection.project_entity(src, target_scope="shared", actor=actor)
        assert _shared_entity_row(ms, e)["share_origin"] == "user"

        # Document cascade on the same entity promotes to 'multiple'.
        pk_a = _seed_personal_file_index(ms, owner, doc_a)
        _relate_to_document(ms, owner, e, doc_a)
        cascade.cascade_share_document_entities(
            src_file={"file_path": doc_a, "user_id": owner}, actor=actor
        )
        _seed_shared_file_index(ms, owner, doc_a, pk_a)
        assert _shared_entity_row(ms, e)["share_origin"] == "multiple"

        # Document unshare: no other shared doc → downgrade, NOT delete.
        ms.execute(
            "DELETE FROM file_index WHERE published_from = %s AND scope = 'shared'", (pk_a,)
        )
        cascade.retract_document_entities(file_path=doc_a, owner_user_id=owner)
        row = _shared_entity_row(ms, e)
        assert row is not None, "user-shared entity wrongly deleted on document unshare"
        assert row["share_origin"] == "user"
        assert _shared_point_exists(vs, e)
    finally:
        _cleanup(ms, vs, owner)


def test_purge_last_shared_doc_leaves_no_shared_entity_orphans(live_stores):
    ms, vs = live_stores
    owner = f"itest-{uuid.uuid4().hex[:8]}"
    file_path = f"/uploads/{owner}/p-{uuid.uuid4().hex[:6]}.md"
    actor = UserContext(user_id=owner, is_authenticated=True)
    try:
        e = _seed_personal_entity(ms, owner, "PurgeMe")
        doc_id = _seed_personal_file_index(ms, owner, file_path)
        _relate_to_document(ms, owner, e, file_path)
        cascade.cascade_share_document_entities(
            src_file={"file_path": file_path, "user_id": owner}, actor=actor
        )
        assert _shared_entity_row(ms, e) is not None

        # Hard purge the (only) source doc. _postgres_arm plans retraction BEFORE
        # deleting entity_relations, then the post-commit sweep clears the point.
        purge_document(user_id=owner, document_id=doc_id)

        assert _shared_entity_row(ms, e) is None, "shared entity orphaned after purge"
        assert not _shared_point_exists(vs, e), "shared Qdrant point orphaned after purge"
    finally:
        _cleanup(ms, vs, owner)
