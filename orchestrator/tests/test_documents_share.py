# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Service-layer tests for household document sharing (LUM-157).

Covers the synchronous share/unshare guards, in-flight coalescing, the
``list_documents`` owner-projection collapse + share-status derivation, and the
member's view of another owner's shared projection. Real projection (Qdrant +
Postgres) is exercised by the compose integration gate, not here.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

import pytest
from auth import UserContext
from services.document_purge import DocumentNotFoundError
from services.documents import DocumentNotSharedError
from services.documents import list_documents
from services.documents import share_document
from services.documents import unshare_document

import config


class _ShareStore:
    """Fake metadata store modelling only the queries the share path issues."""

    def __init__(self) -> None:
        self.file_rows: list[dict] = []
        self.batch_jobs: list[dict] = []

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def fetch_all(self, query: str, params: tuple | None = None):
        q = " ".join(query.split()).lower()
        p = params or ()
        if "from file_index fi" in q:
            # list_documents main query: emulate the visible household union
            # (personal rows for the caller + any shared rows) AND the collapse
            # of the caller's own projection rows.
            caller = p[-2]
            out = []
            for r in self.file_rows:
                if r.get("published_from") is not None and r.get("user_id") == caller:
                    continue  # collapsed: owner sees the personal source instead
                out.append(dict(r))
            return out
        if "distinct published_from" in q:
            uid = p[0]
            return [
                {"published_from": r["published_from"]}
                for r in self.file_rows
                if r.get("scope") == "shared"
                and r.get("user_id") == uid
                and r.get("published_from") is not None
            ]
        if (
            "from user_batch_jobs" in q
            and ("'share_document'" in q or "share_document" in q)
            and "pending" in q
            and "dead" not in q
        ):
            return [
                j
                for j in self.batch_jobs
                if j["kind"] in ("share_document", "unshare_document")
                and j["status"] in ("pending", "running")
            ]
        if "from user_batch_jobs" in q and "dead" in q:
            return []
        if "from user_batch_jobs" in q:
            return []
        return []

    def fetch_one(self, query: str, params: tuple | None = None):
        q = " ".join(query.split()).lower()
        p = params or ()
        if "from file_index" in q and "scope = 'personal'" in q:
            doc_id, uid = p[0], p[1]
            for r in self.file_rows:
                if r["id"] == doc_id and r["user_id"] == uid and r.get("scope") == "personal":
                    return {"id": r["id"]}
            return None
        if "published_from = %s" in q and "scope = 'shared'" in q:
            src_id, uid = p[0], p[1]
            for r in self.file_rows:
                if (
                    r.get("published_from") == src_id
                    and r.get("scope") == "shared"
                    and r.get("user_id") == uid
                ):
                    return {"?column?": 1}
            return None
        return None


@pytest.fixture
def share_ms(monkeypatch: pytest.MonkeyPatch) -> _ShareStore:
    store = _ShareStore()
    config._instances["metadata_store"] = store
    return store


def _user(uid: str = "alice") -> UserContext:
    return UserContext(user_id=uid, is_authenticated=True, role="user")


def _row(**overrides) -> dict:
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


def _patch_enqueue(monkeypatch: pytest.MonkeyPatch, job_id: int = 77) -> list[dict]:
    enqueued: list[dict] = []

    def _enqueue(**kwargs):
        enqueued.append(kwargs)
        return job_id

    monkeypatch.setattr("services.batch_queue.enqueue", _enqueue)
    monkeypatch.setattr("services.ingest_progress.update_ingest_job_progress", lambda **kw: kw)
    return enqueued


# --- share_document guards + enqueue -------------------------------------


def test_share_foreign_document_raises_not_found(share_ms, monkeypatch):
    share_ms.file_rows = [_row(id=1, file_path="/a.pdf", user_id="bob")]
    _patch_enqueue(monkeypatch)
    with pytest.raises(DocumentNotFoundError):
        share_document("alice", 1)


def test_share_non_personal_raises_not_found(share_ms, monkeypatch):
    share_ms.file_rows = [
        _row(id=2, file_path="/a.pdf", user_id="alice", scope="shared", published_from=9)
    ]
    _patch_enqueue(monkeypatch)
    with pytest.raises(DocumentNotFoundError):
        share_document("alice", 2)


def test_share_own_personal_enqueues_share_job(share_ms, monkeypatch):
    share_ms.file_rows = [_row(id=3, file_path="/a.pdf", user_id="alice")]
    enq = _patch_enqueue(monkeypatch, job_id=101)
    resp = share_document("alice", 3)
    assert resp.job_id == 101
    assert resp.share_status == "sharing"
    assert enq[0]["kind"] == "share_document"
    assert enq[0]["payload"] == {"document_id": 3}


def test_share_coalesces_onto_inflight_job(share_ms, monkeypatch):
    share_ms.file_rows = [_row(id=4, file_path="/a.pdf", user_id="alice")]
    share_ms.batch_jobs = [
        {
            "id": 55,
            "kind": "share_document",
            "status": "running",
            "payload": {"document_id": 4},
        }
    ]
    enq = _patch_enqueue(monkeypatch)
    resp = share_document("alice", 4)
    assert resp.job_id == 55  # reused, no new enqueue
    assert resp.share_status == "sharing"
    assert enq == []


def test_unshare_while_share_inflight_enqueues_unshare_not_coalesced(share_ms, monkeypatch):
    """Opposite-direction toggle must not reuse the in-flight share job (LUM-157)."""
    share_ms.file_rows = [_row(id=7, file_path="/a.pdf", user_id="alice")]
    share_ms.batch_jobs = [
        {
            "id": 66,
            "kind": "share_document",
            "status": "running",
            "payload": {"document_id": 7},
        }
    ]
    enq = _patch_enqueue(monkeypatch, job_id=67)
    resp = unshare_document("alice", 7)
    assert resp.job_id == 67
    assert resp.share_status == "unsharing"
    assert enq[0]["kind"] == "unshare_document"
    assert enq[0]["payload"] == {"document_id": 7}


# --- unshare_document guards ---------------------------------------------


def test_unshare_not_shared_raises(share_ms, monkeypatch):
    share_ms.file_rows = [_row(id=5, file_path="/a.pdf", user_id="alice")]
    _patch_enqueue(monkeypatch)
    with pytest.raises(DocumentNotSharedError):
        unshare_document("alice", 5)


def test_unshare_shared_enqueues_unshare_job(share_ms, monkeypatch):
    share_ms.file_rows = [
        _row(id=6, file_path="/a.pdf", user_id="alice"),
        _row(
            id=60,
            file_path="/a.pdf",
            user_id="alice",
            scope="shared",
            published_from=6,
        ),
    ]
    enq = _patch_enqueue(monkeypatch, job_id=202)
    resp = unshare_document("alice", 6)
    assert resp.job_id == 202
    assert resp.share_status == "unsharing"
    assert enq[0]["kind"] == "unshare_document"


# --- list_documents share-status derivation + collapse -------------------


def test_owner_shared_source_marked_shared_and_projection_collapsed(share_ms):
    share_ms.file_rows = [
        _row(id=10, file_path="/report.pdf", user_id="alice"),
        _row(
            id=100,
            file_path="/report.pdf",
            user_id="alice",
            scope="shared",
            published_from=10,
        ),
    ]
    rows = list_documents(_user("alice"), limit=50)
    ids = [r.document_id for r in rows]
    assert ids == [10]  # owner projection collapsed out
    doc = rows[0]
    assert doc.share_status == "shared"
    assert doc.is_shared is True
    assert doc.is_owner is True


def test_member_sees_projection_as_shared_not_owned(share_ms):
    # Alice's projection carries Alice's user_id; Bob is the caller.
    share_ms.file_rows = [
        _row(
            id=100,
            file_path="/report.pdf",
            user_id="alice",
            scope="shared",
            published_from=10,
        ),
    ]
    rows = list_documents(_user("bob"), limit=50)
    assert len(rows) == 1
    doc = rows[0]
    assert doc.share_status == "shared"
    assert doc.is_owner is False


def test_owner_personal_only_is_personal(share_ms):
    share_ms.file_rows = [_row(id=11, file_path="/private.pdf", user_id="alice")]
    rows = list_documents(_user("alice"), limit=50)
    assert rows[0].share_status == "personal"
    assert rows[0].is_shared is False
    assert rows[0].is_owner is True


def test_owner_sees_inflight_sharing_status(share_ms):
    share_ms.file_rows = [_row(id=12, file_path="/x.pdf", user_id="alice")]
    share_ms.batch_jobs = [
        {
            "id": 88,
            "kind": "share_document",
            "status": "pending",
            "payload": {"document_id": 12},
        }
    ]
    rows = list_documents(_user("alice"), limit=50)
    assert rows[0].share_status == "sharing"
    assert rows[0].in_flight_share_job_id == 88
