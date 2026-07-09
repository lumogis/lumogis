# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for ``services/ingest_progress``."""

from __future__ import annotations

import contextlib
from datetime import datetime
from datetime import timezone

import pytest

import config as _config
from services import ingest_progress as ip


def _norm(query: str) -> str:
    return " ".join(query.split()).lower()


class _FakeIngestProgressStore:
    def __init__(self) -> None:
        self.rows: dict[int, dict] = {}

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    @contextlib.contextmanager
    def transaction(self):
        yield

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = _norm(query)
        p = params or ()

        if "update user_batch_jobs" in q and "returning" in q:
            stage, pct, msg, jid, uid = p
            row = self.rows.get(int(jid))
            if not row or row["user_id"] != uid:
                return None
            row["progress_stage"] = stage
            row["progress_pct"] = pct
            row["progress_message"] = msg
            return dict(row)

        if "from user_batch_jobs" in q and "ingest_upload" in q and "where id" in q:
            jid, uid = p
            row = self.rows.get(int(jid))
            if not row or row["user_id"] != uid:
                return None
            if row["kind"] not in ("ingest_upload", "ingest_watch_file"):
                return None
            return dict(row)

        if "count(*) filter" in q and "batch_id" in q:
            uid, bid = p
            completed = failed = in_progress = 0
            for row in self.rows.values():
                if row["user_id"] != uid:
                    continue
                if row["kind"] not in ("ingest_upload", "ingest_watch_file"):
                    continue
                payload = row.get("payload") or {}
                if payload.get("batch_id") != bid:
                    continue
                st = row["status"]
                if st == "done":
                    completed += 1
                elif st == "dead":
                    failed += 1
                elif st in ("pending", "running"):
                    in_progress += 1
            return {"completed": completed, "failed": failed, "in_progress": in_progress}

        return None

    def execute(self, query: str, params: tuple | None = None) -> None:
        pass

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        return []


@pytest.fixture
def progress_store(monkeypatch):
    store = _FakeIngestProgressStore()
    monkeypatch.setitem(_config._instances, "metadata_store", store)
    return store


def _seed_job(store: _FakeIngestProgressStore, **overrides) -> int:
    row = {
        "id": 1,
        "user_id": "alice",
        "kind": "ingest_upload",
        "payload": {"file_id": "fid1", "batch_id": "batch-a"},
        "status": "running",
        "attempt": 0,
        "progress_stage": None,
        "progress_pct": None,
        "progress_message": None,
        "error": None,
        "enqueued_at": datetime.now(timezone.utc),
        "started_at": datetime.now(timezone.utc),
        "finished_at": None,
    }
    row.update(overrides)
    jid = int(row["id"])
    store.rows[jid] = row
    return jid


def test_update_emits_sse_with_returning_row(progress_store, monkeypatch) -> None:
    jid = _seed_job(progress_store)
    emitted: list[tuple[str, dict, str]] = []

    def _capture(event: str, body: dict, *, user_id: str) -> None:
        emitted.append((event, body, user_id))

    monkeypatch.setattr(
        "routes.events.enqueue_user_sse",
        _capture,
    )
    body = ip.update_ingest_job_progress(
        job_id=jid,
        user_id="alice",
        stage="extracting",
    )
    assert body["stage"] == "extracting"
    assert body["progress_pct"] == 15
    assert body["file_id"] == "fid1"
    assert body["batch_id"] == "batch-a"
    assert len(emitted) == 1
    assert emitted[0][0] == "ingest_progress"
    assert emitted[0][1]["job_id"] == jid
    assert emitted[0][2] == "alice"


def test_reset_for_retry_does_not_emit_sse(progress_store, monkeypatch) -> None:
    jid = _seed_job(progress_store)
    calls: list[str] = []
    monkeypatch.setattr(
        "routes.events.enqueue_user_sse",
        lambda *a, **k: calls.append("sse"),
    )
    ip.reset_ingest_job_progress_for_retry(job_id=jid, user_id="alice")
    assert calls == []
    assert progress_store.rows[jid]["progress_stage"] == "queued"


def test_dead_status_maps_to_failed_stage(progress_store) -> None:
    jid = _seed_job(progress_store, status="dead", error="boom", progress_stage=None)
    row = progress_store.rows[jid]
    body = ip.job_row_to_progress_response(row)
    assert body["stage"] == "failed"
    assert body["error"]


def test_batch_summary_empty_batch(progress_store) -> None:
    summary = ip.get_ingest_batch_summary(batch_id="empty-batch", user_id="alice")
    assert summary == {
        "batch_id": "empty-batch",
        "completed": 0,
        "failed": 0,
        "in_progress": 0,
    }


def test_batch_summary_counts(progress_store) -> None:
    progress_store.rows[1] = {
        "id": 1,
        "user_id": "alice",
        "kind": "ingest_upload",
        "payload": {"batch_id": "b1"},
        "status": "done",
    }
    progress_store.rows[2] = {
        "id": 2,
        "user_id": "alice",
        "kind": "ingest_upload",
        "payload": {"batch_id": "b1"},
        "status": "dead",
    }
    progress_store.rows[3] = {
        "id": 3,
        "user_id": "alice",
        "kind": "ingest_upload",
        "payload": {"batch_id": "b1"},
        "status": "pending",
    }
    summary = ip.get_ingest_batch_summary(batch_id="b1", user_id="alice")
    assert summary["completed"] == 1
    assert summary["failed"] == 1
    assert summary["in_progress"] == 1
    assert "total" not in summary


def test_validate_batch_id_rejects_unsafe() -> None:
    with pytest.raises(ValueError):
        ip.validate_batch_id("../evil")
    with pytest.raises(ValueError):
        ip.validate_batch_id("")


def test_cross_user_job_lookup_returns_none(progress_store) -> None:
    jid = _seed_job(progress_store, user_id="alice")
    assert ip.get_ingest_job_row(job_id=jid, user_id="bob") is None


def test_retry_failure_resets_without_sse(progress_store, monkeypatch) -> None:
    jid = _seed_job(progress_store)
    calls: list[str] = []
    monkeypatch.setattr(
        "routes.events.enqueue_user_sse",
        lambda *a, **k: calls.append("sse"),
    )
    ip.maybe_handle_ingest_job_failure(
        job_id=jid,
        user_id="alice",
        kind="ingest_upload",
        new_attempt=1,
        max_attempts=3,
        error="transient",
    )
    assert calls == []
    assert progress_store.rows[jid]["progress_stage"] == "queued"


def test_terminal_failure_emits_failed_sse(progress_store, monkeypatch) -> None:
    jid = _seed_job(progress_store)
    calls: list[str] = []
    monkeypatch.setattr(
        "routes.events.enqueue_user_sse",
        lambda *a, **k: calls.append("sse"),
    )
    ip.maybe_handle_ingest_job_failure(
        job_id=jid,
        user_id="alice",
        kind="ingest_upload",
        new_attempt=3,
        max_attempts=3,
        error="terminal boom",
    )
    assert len(calls) == 1
    assert progress_store.rows[jid]["progress_stage"] == "failed"
