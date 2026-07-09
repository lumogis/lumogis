# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Service-layer tests for services/documents.py (LUM-160)."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

import pytest
from auth import UserContext
from models.api_v1 import DocumentStatus
from services.documents import list_documents
from services.documents import reingest_document

import config


class _ListDocumentsStore:
    def __init__(self) -> None:
        self.file_rows: list[dict] = []
        self.batch_jobs: list[dict] = []

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def fetch_all(self, query: str, params: tuple | None = None):
        q = " ".join(query.split()).lower()
        if "from file_index fi" in q:
            return list(self.file_rows)
        if "from user_batch_jobs" in q and "pending" in q:
            return [j for j in self.batch_jobs if j["status"] in ("pending", "running")]
        if "from user_batch_jobs" in q and "dead" in q:
            return [j for j in self.batch_jobs if j["status"] == "dead"]
        return []

    def fetch_one(self, query: str, params: tuple | None = None):
        q = " ".join(query.split()).lower()
        p = params or ()
        if "from file_index" in q and "scope = 'personal'" in q:
            doc_id, uid = p[0], p[1]
            for row in self.file_rows:
                if row["id"] == doc_id and row["user_id"] == uid and row.get("scope") == "personal":
                    return dict(row)
        return None


@pytest.fixture
def list_ms(monkeypatch: pytest.MonkeyPatch) -> _ListDocumentsStore:
    store = _ListDocumentsStore()
    config._instances["metadata_store"] = store
    return store


def _user(uid: str = "alice") -> UserContext:
    return UserContext(user_id=uid, is_authenticated=True, role="user")


def _file_row(**overrides) -> dict:
    base = {
        "file_type": ".pdf",
        "chunk_count": 2,
        "scope": "personal",
        "updated_at": datetime.now(timezone.utc),
        "published_from": None,
        "entity_count": 0,
    }
    base.update(overrides)
    return base


def test_list_documents_dedupes_inflight_when_indexed(list_ms):
    path = "/uploads/alice/report.pdf"
    list_ms.file_rows = [
        _file_row(id=1, file_path=path, user_id="alice", entity_count=0),
    ]
    list_ms.batch_jobs = [
        {
            "id": 99,
            "kind": "ingest_upload",
            "status": "pending",
            "payload": {"stored_path": path, "original_filename": "report.pdf"},
        }
    ]
    rows = list_documents(_user(), limit=50)
    assert len(rows) == 1
    assert rows[0].document_id == 1
    assert rows[0].status == DocumentStatus.indexing


def test_entity_count_scoped_per_user(list_ms):
    path = "/collision/path.pdf"
    list_ms.file_rows = [
        _file_row(id=1, file_path=path, user_id="alice", entity_count=2),
    ]
    assert list_documents(_user("alice"))[0].entity_count == 2
    list_ms.file_rows = [
        _file_row(id=2, file_path=path, user_id="bob", entity_count=1),
    ]
    assert list_documents(_user("bob"))[0].entity_count == 1


def test_ingest_folder_no_per_file_indexing_row(list_ms):
    list_ms.batch_jobs = [
        {
            "id": 7,
            "kind": "ingest_folder",
            "status": "running",
            "payload": {"path": "/watch/inbox"},
        }
    ]
    rows = list_documents(_user(), limit=50)
    assert rows == []


def test_reingest_force_bypasses_hash_in_payload(list_ms, tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads" / "alice"
    upload_dir.mkdir(parents=True)
    doc = upload_dir / "notes.txt"
    doc.write_text("hello", encoding="utf-8")
    list_ms.file_rows = [
        {
            "id": 5,
            "file_path": str(doc),
            "user_id": "alice",
            "scope": "personal",
        }
    ]
    monkeypatch.setattr(config, "get_uploads_path", lambda: tmp_path / "uploads")
    enqueued: list[dict] = []

    def _enqueue(**kwargs):
        enqueued.append(kwargs)
        return 42

    monkeypatch.setattr("services.batch_queue.enqueue", _enqueue)
    monkeypatch.setattr(
        "services.ingest_progress.update_ingest_job_progress",
        lambda **kw: kw,
    )
    resp = reingest_document("alice", 5, force=True)
    assert resp.job_id == 42
    assert enqueued[0]["payload"]["force"] is True


def test_reingest_skips_unchanged_hash_queues_job(list_ms, tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads" / "alice"
    upload_dir.mkdir(parents=True)
    doc = upload_dir / "same.txt"
    doc.write_text("same", encoding="utf-8")
    list_ms.file_rows = [
        {
            "id": 6,
            "file_path": str(doc),
            "user_id": "alice",
            "scope": "personal",
        }
    ]
    monkeypatch.setattr(config, "get_uploads_path", lambda: tmp_path / "uploads")
    enqueued: list[dict] = []

    def _enqueue(**kwargs):
        enqueued.append(kwargs)
        return 43

    monkeypatch.setattr("services.batch_queue.enqueue", _enqueue)
    monkeypatch.setattr(
        "services.ingest_progress.update_ingest_job_progress",
        lambda **kw: kw,
    )
    resp = reingest_document("alice", 6, force=False)
    assert resp.queued is True
    assert enqueued[0]["payload"]["force"] is False
