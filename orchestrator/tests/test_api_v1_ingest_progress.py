# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-511 — ingest job/batch progress API routes."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("WORKSPACE_PATH", str(tmp_path / "workspace"))
    monkeypatch.setenv("LUMOGIS_UPLOADS_PATH", str(tmp_path / "uploads"))
    import main

    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def auth_headers(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "ingest-progress-test-secret-do-not-use")
    from auth import mint_access_token

    return {"Authorization": f"Bearer {mint_access_token('alice-ip', 'user')}"}


def test_upload_returns_job_id(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("services.batch_queue.enqueue", lambda **_kw: 42)
    monkeypatch.setattr(
        "services.ingest_progress.update_ingest_job_progress",
        lambda **kw: {"job_id": kw["job_id"], "stage": kw["stage"]},
    )
    resp = client.post(
        "/api/v1/ingest/upload",
        headers=auth_headers,
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data["job_id"] == 42
    assert data["file_id"]


def test_upload_batch_header_stored_in_payload(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict] = []

    def _enqueue(**kwargs):
        captured.append(dict(kwargs))
        return 7

    monkeypatch.setattr("services.batch_queue.enqueue", _enqueue)
    monkeypatch.setattr(
        "services.ingest_progress.update_ingest_job_progress",
        lambda **kw: kw,
    )
    batch = "my-batch-01"
    resp = client.post(
        "/api/v1/ingest/upload",
        headers={**auth_headers, "X-Lumogis-Batch-Id": batch},
        files={"file": ("a.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 202
    assert captured[0]["payload"]["batch_id"] == batch


def test_upload_invalid_batch_header_400(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    resp = client.post(
        "/api/v1/ingest/upload",
        headers={**auth_headers, "X-Lumogis-Batch-Id": "../bad"},
        files={"file": ("a.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 400


def test_get_job_not_found(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("services.ingest_progress.get_ingest_job_row", lambda **kw: None)
    resp = client.get("/api/v1/ingest/jobs/999", headers=auth_headers)
    assert resp.status_code == 404


def test_get_job_cross_user_404(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("services.ingest_progress.get_ingest_job_row", lambda **kw: None)
    resp = client.get("/api/v1/ingest/jobs/1", headers=auth_headers)
    assert resp.status_code == 404


def test_get_job_returns_progress(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {
        "id": 5,
        "user_id": "alice-ip",
        "kind": "ingest_upload",
        "payload": {"file_id": "abc"},
        "status": "running",
        "attempt": 0,
        "progress_stage": "embedding",
        "progress_pct": 60,
        "progress_message": None,
        "error": None,
        "enqueued_at": None,
        "started_at": None,
        "finished_at": None,
    }
    monkeypatch.setattr(
        "services.ingest_progress.get_ingest_job_row",
        lambda **kw: row,
    )
    resp = client.get("/api/v1/ingest/jobs/5", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == 5
    assert data["stage"] == "embedding"
    assert data["progress_pct"] == 60


def test_get_batch_summary(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.ingest_progress.get_ingest_batch_summary",
        lambda **kw: {
            "batch_id": kw["batch_id"],
            "completed": 2,
            "failed": 0,
            "in_progress": 1,
        },
    )
    resp = client.get("/api/v1/ingest/batches/batch-xyz", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["batch_id"] == "batch-xyz"
    assert data["completed"] == 2
    assert "total" not in data
