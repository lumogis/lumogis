# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live two-user retrieval integration for shared-document content projection (LUM-157).

This is the DoD gate that the port-level unit tests
(``tests/unit/test_document_share_projection.py``) cannot cover: it runs against
a **real Qdrant + Postgres** (inside the compose ``orchestrator`` container) and
proves the reuse-vectors mechanism end to end:

* **project** — sharing a personal document mirrors its Qdrant chunks into
  ``scope='shared'`` (reusing the stored vectors, no re-embed) plus the
  ``file_index`` shared projection row;
* **two-user retrieval** — a *different* household member's visibility filter
  actually matches those shared chunks (the "visible but not retrievable" bug
  the plan set out to fix);
* **scoped delete / no match-all** — unshare removes only the shared copies,
  leaving the owner's personal chunks intact;
* **no orphans on source purge** — purging the personal source removes the
  shared projection chunks too.

Skips cleanly when a real Qdrant-backed store is not reachable (e.g. a plain
unit run with no stack), so it is safe in the default suite and only exercises
the real path under ``make compose-test`` / ``make compose-test-integration``.
"""

from __future__ import annotations

import uuid

import pytest
from auth import UserContext
from services import projection
from services.document_purge import purge_document
from visibility import visible_qdrant_filter

import config

pytestmark = pytest.mark.integration

COLLECTION = "documents"


def _vector_dim() -> int:
    try:
        return int(config.get_embedder().vector_size)
    except Exception:
        return 768  # ollama nomic-embed-text default; matches collection config


@pytest.fixture
def live_stores(monkeypatch):
    """Yield (metadata_store, vector_store) wired to the REAL stack, or skip.

    ``tests/conftest.py`` installs an autouse fixture that swaps
    ``config._instances`` for in-memory fakes. This fixture runs *after* that
    autouse fixture and deliberately overrides those two entries with the real
    Qdrant + Postgres adapters (built from the container's env), so the
    projection engine under test talks to the real stores. Skips cleanly when
    the real stack is not reachable (e.g. a plain host unit run).
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
            vs.count(COLLECTION)
        except Exception:
            vs.create_collection(COLLECTION, _vector_dim())
            vs.count(COLLECTION)
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"real stack not reachable: {exc}")

    monkeypatch.setitem(config._instances, "vector_store", vs)
    monkeypatch.setitem(config._instances, "metadata_store", ms)
    return ms, vs


def _seed_personal_document(ms, vs, owner: str, file_path: str, n_chunks: int) -> int:
    """Insert a personal file_index row + n personal Qdrant chunks; return doc id."""
    row = ms.fetch_one(
        """
        INSERT INTO file_index (file_path, file_hash, file_type, chunk_count, user_id, scope)
        VALUES (%s, %s, %s, %s, %s, 'personal')
        RETURNING id
        """,
        (file_path, "hash-" + uuid.uuid4().hex, ".md", n_chunks, owner),
    )
    doc_id = int(row["id"])
    dim = _vector_dim()
    for i in range(n_chunks):
        # Distinct-ish vector per chunk; chunk 0 is the query anchor (cosine ~1).
        vec = [0.0] * dim
        vec[i % dim] = 1.0
        vs.upsert(
            collection=COLLECTION,
            id=str(uuid.uuid4()),
            vector=vec,
            payload={
                "user_id": owner,
                "file_path": file_path,
                "chunk_index": i,
                "text": f"secret pangram chunk {i} {uuid.uuid4().hex}",
                "scope": "personal",
            },
        )
    return doc_id


def _query_vector(dim: int) -> list[float]:
    v = [0.0] * dim
    v[0] = 1.0
    return v


def _shared_points_for(vs, owner: str, doc_id: int) -> list[dict]:
    pts = vs.scroll_collection(COLLECTION, user_id=owner, with_vectors=False)
    return [
        p
        for p in pts
        if (p.get("payload") or {}).get("scope") == "shared"
        and (p.get("payload") or {}).get("published_from") == doc_id
    ]


def _cleanup(ms, vs, owner: str, file_path: str) -> None:
    try:
        vs.delete_where(
            COLLECTION,
            {"must": [{"key": "user_id", "match": {"value": owner}}]},
        )
    except Exception:
        pass
    try:
        ms.execute("DELETE FROM file_index WHERE user_id = %s", (owner,))
    except Exception:
        pass


def test_share_projects_and_second_member_can_retrieve(live_stores):
    ms, vs = live_stores
    owner = f"itest-owner-{uuid.uuid4().hex[:8]}"
    member = f"itest-member-{uuid.uuid4().hex[:8]}"
    file_path = f"/uploads/{owner}/lum157-{uuid.uuid4().hex[:8]}.md"
    actor = UserContext(user_id=owner, is_authenticated=True)
    try:
        doc_id = _seed_personal_document(ms, vs, owner, file_path, n_chunks=3)

        # Before sharing: a different member's visibility filter must NOT match
        # the owner's personal chunks.
        dim = _vector_dim()
        member_ctx = UserContext(user_id=member, is_authenticated=True)
        pre = vs.search(
            COLLECTION,
            _query_vector(dim),
            limit=10,
            threshold=0.0,
            filter=visible_qdrant_filter(member_ctx),
        )
        pre_paths = {(r.get("payload") or {}).get("file_path") for r in pre}
        assert file_path not in pre_paths, "member saw the doc before it was shared"

        # Share (reuse-vectors projection).
        row, projected, failed = projection.project_file_with_status(
            {
                "id": doc_id,
                "user_id": owner,
                "file_path": file_path,
                "file_hash": "h",
                "file_type": ".md",
                "chunk_count": 3,
            },
            target_scope="shared",
            actor=actor,
        )
        assert projected == 3 and failed == 0
        assert row.get("scope") == "shared" and int(row.get("published_from")) == doc_id

        shared_pts = _shared_points_for(vs, owner, doc_id)
        assert len(shared_pts) == 3, "expected 3 shared chunk projections"

        # Two-user retrieval: the member's filter now matches the shared chunks.
        post = vs.search(
            COLLECTION,
            _query_vector(dim),
            limit=10,
            threshold=0.0,
            filter=visible_qdrant_filter(member_ctx),
        )
        post_shared = [
            r
            for r in post
            if (r.get("payload") or {}).get("file_path") == file_path
            and (r.get("payload") or {}).get("scope") == "shared"
        ]
        assert post_shared, "member could not retrieve the shared document chunks"
    finally:
        _cleanup(ms, vs, owner, file_path)


def test_second_member_document_chat_http_returns_shared_citations(live_stores, monkeypatch):
    """LUM-157 P1 (second gap) — the **HTTP** ``POST /api/v1/chat/completions``
    round-trip *as a second household member* returns citations grounded in the
    **owner's shared document**.

    The other live tests prove sharing at the ``vs.search`` retrieval layer; this
    one drives the real chat route (``resolve_document_file_path`` +
    ``build_injected_context`` → ``retrieve_document_context`` → live Qdrant) as a
    *different* member, so document-scoped chat honours shared visibility end to
    end over HTTP — not just in the projection engine.

    Sharing is about *retrieval*, not the generated answer, and cloud models are
    privacy-mode-blocked in the local stack, so only the LLM (``ask``) and the
    privacy/model gate are stubbed. The reranker is disabled so the deterministic
    bi-encoder floor path is used (querying the exact stored sentence ≈ cosine 1.0).
    """
    import routes.api_v1.chat as v1_chat
    import services.privacy_mode as privacy_mode
    from authz import require_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    ms, vs = live_stores
    owner = f"itest-owner-{uuid.uuid4().hex[:8]}"
    member = f"itest-member-{uuid.uuid4().hex[:8]}"
    file_path = f"/uploads/{owner}/lum157-chat-{uuid.uuid4().hex[:8]}.md"
    actor = UserContext(user_id=owner, is_authenticated=True)
    member_ctx = UserContext(user_id=member, is_authenticated=True)

    # A distinctive sentence: querying it back embeds to (near) the stored vector,
    # so real retrieval deterministically surfaces the single shared chunk.
    marker = uuid.uuid4().hex
    sentence = (
        f"The household budget code word is {marker}; "
        "sphinx of black quartz, judge my vow."
    )

    try:
        embedder = config.get_embedder()
        # Personal source: one real-embedded chunk owned by A.
        row = ms.fetch_one(
            """
            INSERT INTO file_index (file_path, file_hash, file_type, chunk_count, user_id, scope)
            VALUES (%s, %s, %s, %s, %s, 'personal')
            RETURNING id
            """,
            (file_path, "hash-" + uuid.uuid4().hex, ".md", 1, owner),
        )
        doc_id = int(row["id"])
        vs.upsert(
            collection=COLLECTION,
            id=str(uuid.uuid4()),
            vector=embedder.embed(sentence),
            payload={
                "user_id": owner,
                "file_path": file_path,
                "chunk_index": 0,
                "text": sentence,
                "scope": "personal",
            },
        )

        # A publishes → shared projection (reuse-vectors, no re-embed).
        _row, projected, failed = projection.project_file_with_status(
            {
                "id": doc_id,
                "user_id": owner,
                "file_path": file_path,
                "file_hash": "h",
                "file_type": ".md",
                "chunk_count": 1,
            },
            target_scope="shared",
            actor=actor,
        )
        assert projected == 1 and failed == 0

        shared_row = ms.fetch_one(
            "SELECT id FROM file_index WHERE published_from = %s AND scope = 'shared'",
            (doc_id,),
        )
        assert shared_row, "shared projection file_index row missing"
        shared_id = int(shared_row["id"])

        # --- HTTP round-trip AS MEMBER B --------------------------------------
        monkeypatch.setattr(config, "get_reranker", lambda: None)
        monkeypatch.setattr(v1_chat, "get_user", lambda request: member_ctx)
        monkeypatch.setattr(v1_chat, "ask", lambda *a, **kw: "ANSWER")
        monkeypatch.setattr(privacy_mode, "blocks_remote_models", lambda user_id: False)
        monkeypatch.setattr(
            privacy_mode, "resolve_model_for_request", lambda model, user_id: (model, None)
        )
        monkeypatch.setattr(
            config,
            "is_model_enabled",
            lambda model, *, user_id=None, _privacy_blocks_remote=False: True,
        )

        app = FastAPI()
        app.dependency_overrides[require_user] = lambda: member_ctx
        app.include_router(v1_chat.router)
        client = TestClient(app)

        resp = client.post(
            "/api/v1/chat/completions",
            json={
                "model": "claude",
                "stream": False,
                "document_id": shared_id,
                "messages": [{"role": "user", "content": sentence}],
            },
        )
        assert resp.status_code == 200, resp.text[:800]
        body = resp.json()
        lumogis = body.get("lumogis")
        assert lumogis, f"member chat must carry the lumogis extension block: {body}"
        citations = lumogis.get("context_citations") or []
        assert citations, "member scoped chat returned no document citations"
        assert any(
            (c.get("file_path") or "") == file_path for c in citations
        ), f"member citations did not reference the owner's shared document: {citations}"
    finally:
        _cleanup(ms, vs, owner, file_path)


def test_unshare_removes_only_shared_copies(live_stores):
    ms, vs = live_stores
    owner = f"itest-owner-{uuid.uuid4().hex[:8]}"
    file_path = f"/uploads/{owner}/lum157-{uuid.uuid4().hex[:8]}.md"
    actor = UserContext(user_id=owner, is_authenticated=True)
    try:
        doc_id = _seed_personal_document(ms, vs, owner, file_path, n_chunks=2)
        projection.project_file_with_status(
            {"id": doc_id, "user_id": owner, "file_path": file_path, "chunk_count": 2},
            target_scope="shared",
            actor=actor,
        )
        assert len(_shared_points_for(vs, owner, doc_id)) == 2

        removed = projection.unproject_file(doc_id, target_scope="shared")
        assert removed == 1
        assert _shared_points_for(vs, owner, doc_id) == []

        # Personal originals intact (scoped delete, never match-all).
        personal = [
            p
            for p in vs.scroll_collection(COLLECTION, user_id=owner)
            if (p.get("payload") or {}).get("scope") == "personal"
        ]
        assert len(personal) == 2, "unshare must not touch the owner's personal chunks"
        # Personal file_index row still present.
        assert ms.fetch_one(
            "SELECT id FROM file_index WHERE id = %s AND scope = 'personal'", (doc_id,)
        )
    finally:
        _cleanup(ms, vs, owner, file_path)


def test_purging_shared_source_leaves_no_shared_orphans(live_stores):
    ms, vs = live_stores
    owner = f"itest-owner-{uuid.uuid4().hex[:8]}"
    file_path = f"/uploads/{owner}/lum157-{uuid.uuid4().hex[:8]}.md"
    actor = UserContext(user_id=owner, is_authenticated=True)
    try:
        doc_id = _seed_personal_document(ms, vs, owner, file_path, n_chunks=2)
        projection.project_file_with_status(
            {"id": doc_id, "user_id": owner, "file_path": file_path, "chunk_count": 2},
            target_scope="shared",
            actor=actor,
        )
        assert len(_shared_points_for(vs, owner, doc_id)) == 2

        purge_document(user_id=owner, document_id=doc_id)

        # No shared orphans keyed by published_from remain.
        assert _shared_points_for(vs, owner, doc_id) == []
        # And the personal chunks were swept too.
        remaining = [
            p
            for p in vs.scroll_collection(COLLECTION, user_id=owner)
            if (p.get("payload") or {}).get("file_path") == file_path
        ]
        assert remaining == []
    finally:
        _cleanup(ms, vs, owner, file_path)
