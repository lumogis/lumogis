# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-157 finding-1: the raw ``POST /api/v1/files/{id}/publish`` route must not
project a large document's content synchronously on the request thread.

Documents whose ``chunk_count`` exceeds ``LUMOGIS_SHARE_INLINE_MAX_CHUNKS`` are
routed to the background ``share_document`` job (202-style ``queued`` response);
small documents keep the fast inline projection path. Scope validation (400) and
owner/personal fetch (404) still fail-fast before either path.
"""

from __future__ import annotations

import pytest
import routes.scope as scope_mod
from auth import UserContext
from fastapi import FastAPI
from fastapi.testclient import TestClient
from models.api_v1 import ShareQueuedResponse

import config
from services import documents as doc_svc


class _FileStore:
    """Minimal metadata store returning one personal file_index row."""

    def __init__(self, chunk_count: int):
        self.chunk_count = chunk_count

    def fetch_one(self, query: str, params=None):
        q = " ".join(query.split()).lower()
        if q.startswith("select * from file_index") and "scope = 'personal'" in q:
            return {
                "id": int(params[0]),
                "user_id": params[1],
                "scope": "personal",
                "file_path": "/m.pdf",
                "chunk_count": self.chunk_count,
                "file_hash": "h",
                "file_type": "pdf",
                "ocr_used": False,
            }
        return None


class _EmptyStore:
    def fetch_one(self, query: str, params=None):
        return None


@pytest.fixture
def app():
    from authz import require_user
    from routes.scope import router

    application = FastAPI()
    application.include_router(router)
    ctx = UserContext(user_id="dad", is_authenticated=True)
    application.dependency_overrides[require_user] = lambda: ctx
    return application


def test_large_document_routes_to_background_job(app, monkeypatch):
    monkeypatch.setenv("LUMOGIS_SHARE_INLINE_MAX_CHUNKS", "10")
    monkeypatch.setattr(config, "get_metadata_store", lambda: _FileStore(50))

    called: dict = {}

    def fake_share(user_id, document_id):
        called["args"] = (user_id, document_id)
        return ShareQueuedResponse(document_id=document_id, job_id=777, share_status="sharing")

    monkeypatch.setattr(doc_svc, "share_document", fake_share)
    monkeypatch.setattr(
        scope_mod,
        "_publish_one",
        lambda **k: pytest.fail("inline projection path taken for a large document"),
    )

    resp = TestClient(app).post("/api/v1/files/101/publish", json={"scope": "shared"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["queued"] is True
    assert body["job_id"] == 777
    assert called["args"] == ("dad", 101)


def test_small_document_uses_inline_path(app, monkeypatch):
    monkeypatch.setenv("LUMOGIS_SHARE_INLINE_MAX_CHUNKS", "50")
    monkeypatch.setattr(config, "get_metadata_store", lambda: _FileStore(3))

    monkeypatch.setattr(
        doc_svc,
        "share_document",
        lambda *a, **k: pytest.fail("small document must not enqueue a job"),
    )
    monkeypatch.setattr(
        scope_mod,
        "_publish_one",
        lambda **k: {"resource": "files", "scope": "shared", "id": 3},
    )

    resp = TestClient(app).post("/api/v1/files/3/publish", json={"scope": "shared"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("queued") is None
    assert body["resource"] == "files"


def test_invalid_scope_rejected_before_routing(app, monkeypatch):
    monkeypatch.setattr(config, "get_metadata_store", lambda: _FileStore(500))
    resp = TestClient(app).post("/api/v1/files/5/publish", json={"scope": "system"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_scope"


def test_foreign_or_missing_file_404(app, monkeypatch):
    monkeypatch.setattr(config, "get_metadata_store", lambda: _EmptyStore())
    resp = TestClient(app).post("/api/v1/files/999/publish", json={"scope": "shared"})
    assert resp.status_code == 404
