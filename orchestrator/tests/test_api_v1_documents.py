# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for /api/v1/documents (LUM-160)."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from models.api_v1 import DocumentDetail
from models.api_v1 import DocumentStatus
from models.api_v1 import DocumentSummary
from models.api_v1 import ReingestQueuedResponse
from services.document_purge import DocumentNotFoundError
from services.memory_purge import PurgeResult


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


def _summary(**kwargs) -> DocumentSummary:
    defaults = {
        "document_id": 1,
        "display_name": "doc.pdf",
        "file_path": "/uploads/default/doc.pdf",
        "file_type": ".pdf",
        "chunk_count": 3,
        "entity_count": 1,
        "scope": "personal",
        "status": DocumentStatus.indexed,
        "indexed_at": datetime.now(timezone.utc),
        "error_message": None,
    }
    defaults.update(kwargs)
    return DocumentSummary(**defaults)


def test_list_documents_empty(client):
    with patch("services.documents.list_documents", return_value=[]):
        resp = client.get("/api/v1/documents")
    assert resp.status_code == 200
    assert resp.json()["documents"] == []


def test_list_documents_derived_indexed(client):
    with patch(
        "services.documents.list_documents",
        return_value=[_summary(status=DocumentStatus.indexed, chunk_count=3)],
    ):
        resp = client.get("/api/v1/documents")
    body = resp.json()
    assert body["documents"][0]["status"] == "indexed"


def test_list_documents_derived_indexing(client):
    row = _summary(
        document_id=None,
        in_flight_job_id=42,
        status=DocumentStatus.indexing,
        chunk_count=0,
    )
    with patch("services.documents.list_documents", return_value=[row]):
        resp = client.get("/api/v1/documents")
    body = resp.json()["documents"][0]
    assert body["document_id"] is None
    assert body["in_flight_job_id"] == 42
    assert body["status"] == "indexing"


def test_get_document_entities(client):
    detail = DocumentDetail(
        **_summary().model_dump(),
        file_hash="abc",
        entities=[
            {"entity_id": "ent-1", "name": "Ada", "entity_type": "PERSON"},
        ],
        source_available=True,
    )
    with patch("services.documents.get_document", return_value=detail):
        resp = client.get("/api/v1/documents/1")
    assert resp.status_code == 200
    assert len(resp.json()["entities"]) == 1


def test_delete_document_personal_only(client):
    with patch("services.documents.delete_document", side_effect=DocumentNotFoundError(2)):
        resp = client.delete("/api/v1/documents/2")
    assert resp.status_code == 404


def test_delete_document_two_user_isolation(client):
    with patch("services.documents.delete_document", side_effect=DocumentNotFoundError(1)):
        resp = client.delete("/api/v1/documents/1")
    assert resp.status_code == 404


def test_delete_returns_partial(client):
    with patch(
        "services.documents.delete_document",
        return_value=PurgeResult(postgres_deleted=True, qdrant_deleted=False, errors=["qdrant: x"]),
    ):
        resp = client.delete("/api/v1/documents/1")
    assert resp.status_code == 200
    assert resp.json()["partial"] is True


def test_reingest_source_unavailable(client):
    from services.documents import SourceUnavailableError

    with patch(
        "services.documents.reingest_document",
        side_effect=SourceUnavailableError("/missing.pdf"),
    ):
        resp = client.post("/api/v1/documents/1/reingest", json={"force": False})
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "source_unavailable"


def test_reingest_queued(client):
    with patch(
        "services.documents.reingest_document",
        return_value=ReingestQueuedResponse(document_id=1, job_id=9, queued=True),
    ):
        resp = client.post("/api/v1/documents/1/reingest", json={"force": True})
    assert resp.status_code == 202
    assert resp.json()["job_id"] == 9


def test_publish_document_queued(client):
    from models.api_v1 import ShareQueuedResponse

    with patch(
        "services.documents.share_document",
        return_value=ShareQueuedResponse(document_id=1, job_id=77, share_status="sharing"),
    ):
        resp = client.post("/api/v1/documents/1/publish", json={})
    assert resp.status_code == 202
    body = resp.json()
    assert body["job_id"] == 77
    assert body["share_status"] == "sharing"


def test_publish_foreign_document_404(client):
    with patch(
        "services.documents.share_document",
        side_effect=DocumentNotFoundError(2),
    ):
        resp = client.post("/api/v1/documents/2/publish", json={})
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "document_not_found"


def test_publish_invalid_scope_400(client):
    resp = client.post("/api/v1/documents/1/publish", json={"scope": "public"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_scope"


def test_unpublish_document_queued(client):
    from models.api_v1 import ShareQueuedResponse

    with patch(
        "services.documents.unshare_document",
        return_value=ShareQueuedResponse(
            document_id=1, job_id=88, share_status="unsharing"
        ),
    ):
        resp = client.delete("/api/v1/documents/1/publish")
    assert resp.status_code == 202
    assert resp.json()["share_status"] == "unsharing"


def test_unpublish_not_shared_404(client):
    from services.documents import DocumentNotSharedError

    with patch(
        "services.documents.unshare_document",
        side_effect=DocumentNotSharedError(1),
    ):
        resp = client.delete("/api/v1/documents/1/publish")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "document_not_found"


@pytest.mark.parametrize("bad_id", ["abc", "-1", "0"])
def test_invalid_document_id(client, bad_id):
    resp = client.get(f"/api/v1/documents/{bad_id}")
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_document_id"


@pytest.mark.parametrize("bad_limit", [0, 101])
def test_list_invalid_limit(client, bad_limit):
    resp = client.get(f"/api/v1/documents?limit={bad_limit}")
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_limit"
