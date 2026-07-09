# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live admin_unshare with real Postgres + Qdrant teardown + audit (LUM-584 P1).

Member Bob shares a document; admin Carol retracts it end-to-end through
``admin_unshare``: shared Qdrant chunks are gone, Bob's personal source row
and personal chunks remain, and an ``audit_log`` row is written.

Skips when the real stack is unreachable. Primary gate: ``make compose-test``.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager

import pytest
from auth import UserContext
from fastapi.testclient import TestClient
from visibility import visible_qdrant_filter

import config
from services import admin_unshare as admin_svc
from services import projection

pytestmark = pytest.mark.integration

COLLECTION = "documents"


def _vector_dim() -> int:
    try:
        return int(config.get_embedder().vector_size)
    except Exception:
        return 768


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "admin-unshare-live-access-secret")
    monkeypatch.setenv("LUMOGIS_JWT_REFRESH_SECRET", "admin-unshare-live-refresh-secret")
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("REFRESH_TOKEN_TTL_SECONDS", "3600")
    monkeypatch.setenv("LUMOGIS_REFRESH_COOKIE_SECURE", "false")
    yield
    from routes.auth import _reset_rate_limit_for_tests

    _reset_rate_limit_for_tests()


@pytest.fixture
def live_stores(monkeypatch, auth_env):
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
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"real stack not reachable — run under make compose-test: {exc}")

    monkeypatch.setitem(config._instances, "vector_store", vs)
    monkeypatch.setitem(config._instances, "metadata_store", ms)
    return ms, vs


@contextmanager
def _booted_client():
    import main

    with TestClient(main.app) as client:
        yield client


def _create_user(email: str, role: str) -> str:
    import services.users as users_svc

    return users_svc.create_user(email, "verylongpassword12", role).id


def _login(client: TestClient, email: str) -> str:
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "verylongpassword12"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth_hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_personal_document(ms, vs, owner: str, file_path: str) -> int:
    row = ms.fetch_one(
        """
        INSERT INTO file_index (file_path, file_hash, file_type, chunk_count, user_id, scope)
        VALUES (%s, %s, %s, %s, %s, 'personal')
        RETURNING id
        """,
        (file_path, "hash-" + uuid.uuid4().hex, ".md", 2, owner),
    )
    doc_id = int(row["id"])
    dim = _vector_dim()
    for i in range(2):
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
                "text": f"personal chunk {i} {uuid.uuid4().hex}",
                "scope": "personal",
            },
        )
    return doc_id


def _shared_chunk_count(vs, doc_id: int) -> int:
    return vs.count_where(
        COLLECTION,
        {
            "must": [
                {"key": "published_from", "match": {"value": doc_id}},
                {"key": "scope", "match": {"value": "shared"}},
            ]
        },
    )


def _personal_chunk_count(vs, owner: str, file_path: str) -> int:
    pts = vs.scroll_collection(COLLECTION, user_id=owner, with_vectors=False)
    return sum(
        1
        for p in pts
        if (p.get("payload") or {}).get("file_path") == file_path
        and (p.get("payload") or {}).get("scope") == "personal"
    )


def _cleanup(ms, vs, *user_ids: str, file_path: str | None = None) -> None:
    if file_path:
        try:
            vs.delete_where(
                COLLECTION,
                {"must": [{"key": "file_path", "match": {"value": file_path}}]},
            )
        except Exception:
            pass
        try:
            ms.execute("DELETE FROM file_index WHERE file_path = %s", (file_path,))
        except Exception:
            pass
    for uid in user_ids:
        try:
            ms.execute("DELETE FROM users WHERE id = %s", (uid,))
        except Exception:
            pass
    try:
        ms.execute("DELETE FROM audit_log WHERE connector = 'admin' AND action_name = 'admin_unshare'")
    except Exception:
        pass


def test_live_admin_unshare_retracts_qdrant_and_audits(live_stores):
    ms, vs = live_stores
    suffix = uuid.uuid4().hex[:8]
    bob_email = f"itest-bob-unshare-{suffix}@home.lan"
    carol_email = f"itest-carol-admin-{suffix}@home.lan"
    bob_id = _create_user(bob_email, "user")
    carol_id = _create_user(carol_email, "admin")
    file_path = f"/uploads/{bob_id}/lum584-{suffix}.md"
    doc_id: int | None = None

    try:
        doc_id = _seed_personal_document(ms, vs, bob_id, file_path)
        actor = UserContext(user_id=bob_id, is_authenticated=True, role="user")
        row, projected, failed = projection.project_file_with_status(
            {
                "id": doc_id,
                "user_id": bob_id,
                "file_path": file_path,
                "file_type": ".md",
                "chunk_count": 2,
            },
            target_scope="shared",
            actor=actor,
        )
        assert projected == 2 and failed == 0
        assert _shared_chunk_count(vs, doc_id) >= 1

        admin = UserContext(user_id=carol_id, role="admin", is_authenticated=True)
        result = admin_svc.admin_unshare(actor=admin, resource="files", pk=str(doc_id))
        assert result["unshared"] is True
        assert result["source_owner_id"] == bob_id
        assert _shared_chunk_count(vs, doc_id) == 0

        personal_row = ms.fetch_one(
            "SELECT id, scope FROM file_index WHERE id = %s AND user_id = %s",
            (doc_id, bob_id),
        )
        assert personal_row is not None
        assert personal_row["scope"] == "personal"
        assert _personal_chunk_count(vs, bob_id, file_path) == 2

        audit_rows = ms.fetch_all(
            "SELECT action_name, user_id, result_summary FROM audit_log "
            "WHERE action_name = 'admin_unshare' AND user_id = %s ORDER BY id DESC LIMIT 1",
            (carol_id,),
        )
        assert len(audit_rows) == 1
        payload = json.loads(audit_rows[0]["result_summary"])
        assert payload["resource_type"] == "files"
        assert payload["source_owner_id"] == bob_id

        member_ctx = UserContext(user_id=f"other-{suffix}", is_authenticated=True, role="user")
        dim = _vector_dim()
        query = [0.0] * dim
        query[0] = 1.0
        hits = vs.search(
            COLLECTION,
            query,
            limit=20,
            threshold=0.0,
            filter=visible_qdrant_filter(member_ctx),
        )
        paths = {(h.get("payload") or {}).get("file_path") for h in hits}
        assert file_path not in paths

        with _booted_client() as client:
            carol_token = _login(client, carol_email)
            route = client.delete(
                f"/api/v1/admin/shared-items/files/{doc_id}",
                headers=_auth_hdr(carol_token),
            )
            assert route.status_code == 404
    finally:
        _cleanup(ms, vs, bob_id, carol_id, file_path=file_path)
