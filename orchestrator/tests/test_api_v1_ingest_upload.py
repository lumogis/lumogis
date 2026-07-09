# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-397 C4 — ``POST /api/v1/ingest/upload``."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from models.ingest import IngestResult
from services.batch_handlers.ingest_upload import IngestUploadPayload
from services.batch_handlers.ingest_upload import handle as ingest_upload_handle

from services import batch_queue


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
    monkeypatch.setenv("AUTH_SECRET", "ingest-upload-test-secret-do-not-use")
    from auth import mint_access_token

    return {"Authorization": f"Bearer {mint_access_token('alice-up', 'user')}"}


def test_upload_requires_auth(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "ingest-upload-test-secret-do-not-use")
    resp = client.post(
        "/api/v1/ingest/upload",
        files={"file": ("doc.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 401


def test_upload_unsupported_extension(client: TestClient, auth_headers: dict[str, str]) -> None:
    resp = client.post(
        "/api/v1/ingest/upload",
        headers=auth_headers,
        files={"file": ("malware.exe", b"x", "application/octet-stream")},
    )
    assert resp.status_code == 415


def test_upload_oversize(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config

    monkeypatch.setattr(config, "get_inbox_max_file_bytes", lambda: 5)
    resp = client.post(
        "/api/v1/ingest/upload",
        headers=auth_headers,
        files={"file": ("big.txt", b"123456789", "text/plain")},
    )
    assert resp.status_code == 413


def test_upload_returns_202_and_stores_file(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    enqueued: list[dict] = []

    def _capture_enqueue(*, user_id, kind, payload, run_after=None):
        enqueued.append({"user_id": user_id, "kind": kind, "payload": dict(payload)})
        return 1

    monkeypatch.setattr("services.batch_queue.enqueue", _capture_enqueue)
    monkeypatch.setattr(
        "services.ingest_progress.update_ingest_job_progress",
        lambda **kw: {"job_id": kw["job_id"], "stage": kw["stage"]},
    )
    body = b"hello ingest upload"
    resp = client.post(
        "/api/v1/ingest/upload",
        headers=auth_headers,
        files={"file": ("notes.txt", body, "text/plain")},
    )
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data["status"] == "queued"
    assert data["file_id"]
    assert data["job_id"] == 1
    assert len(enqueued) == 1
    assert enqueued[0]["user_id"] == "alice-up"
    assert enqueued[0]["kind"] == "ingest_upload"
    stored = Path(enqueued[0]["payload"]["stored_path"])
    assert stored.exists()
    assert stored.read_bytes() == body
    assert stored.name.startswith(f"{data['file_id']}_")
    assert stored.parent.name == "alice-up"


def test_upload_wrong_user_isolation(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "ingest-upload-test-secret-do-not-use")
    from auth import mint_access_token

    alice_h = {"Authorization": f"Bearer {mint_access_token('alice-iso', 'user')}"}
    bob_h = {"Authorization": f"Bearer {mint_access_token('bob-iso', 'user')}"}
    enqueued: list[str] = []

    def _capture_enqueue(*, user_id, kind, payload, run_after=None):
        enqueued.append(user_id)
        return 1

    monkeypatch.setattr("services.batch_queue.enqueue", _capture_enqueue)
    monkeypatch.setattr(
        "services.ingest_progress.update_ingest_job_progress",
        lambda **kw: kw,
    )
    resp = client.post(
        "/api/v1/ingest/upload",
        headers=alice_h,
        files={"file": ("a.txt", b"alice only", "text/plain")},
    )
    assert resp.status_code == 202
    assert enqueued == ["alice-iso"]
    import config

    assert list((config.get_uploads_path() / "alice-iso").glob("*"))
    assert not list((config.get_uploads_path() / "bob-iso").glob("*"))
    assert (
        client.post(
            "/api/v1/ingest/upload",
            headers=bob_h,
            files={"file": ("b.txt", b"bob", "text/plain")},
        ).status_code
        == 202
    )


def test_upload_enqueue_failure_cleans_temp(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config

    def _boom(**_kwargs):
        raise RuntimeError("queue down")

    monkeypatch.setattr("services.batch_queue.enqueue", _boom)
    resp = client.post(
        "/api/v1/ingest/upload",
        headers=auth_headers,
        files={"file": ("gone.txt", b"data", "text/plain")},
    )
    assert resp.status_code == 500
    assert list(config.get_uploads_path().rglob("gone.txt")) == []
    assert list(config.get_uploads_path().rglob("*_gone.txt")) == []


def test_ingest_upload_handler_retains_stored_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = tmp_path / "alice-up" / "fid_notes.txt"
    stored.parent.mkdir(parents=True)
    stored.write_text("persist me", encoding="utf-8")

    def _noop_ingest(
        path: str,
        *,
        user_id: str,
        force: bool = False,
        on_progress=None,
    ) -> IngestResult:
        return IngestResult(file_path=path, chunk_count=0, skipped=True)

    monkeypatch.setattr("services.batch_handlers.ingest_upload.ingest_file", _noop_ingest)
    monkeypatch.setattr(
        "services.ingest_progress.update_ingest_job_progress",
        lambda **kw: kw,
    )
    ingest_upload_handle(
        user_id="alice-up",
        job_id=1,
        payload=IngestUploadPayload(
            stored_path=str(stored),
            file_id="fid",
            original_filename="notes.txt",
        ),
    )
    assert stored.exists()
    assert stored.read_text(encoding="utf-8") == "persist me"


def test_upload_same_file_twice_dedup_on_same_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second ``ingest_file`` on the same stored path skips unchanged content."""
    path = tmp_path / "doc.txt"
    path.write_text("same content", encoding="utf-8")
    skipped_flags: list[bool] = []

    def _fake_ingest(
        file_path: str,
        *,
        user_id: str,
        force: bool = False,
        on_progress=None,
    ) -> IngestResult:
        skipped = len(skipped_flags) > 0
        skipped_flags.append(skipped)
        return IngestResult(file_path=file_path, chunk_count=0, skipped=skipped)

    monkeypatch.setattr("services.batch_handlers.ingest_upload.ingest_file", _fake_ingest)
    monkeypatch.setattr(
        "services.ingest_progress.update_ingest_job_progress",
        lambda **kw: kw,
    )
    payload = IngestUploadPayload(stored_path=str(path), file_id="a", original_filename="doc.txt")
    ingest_upload_handle(user_id="u1", job_id=1, payload=payload)
    ingest_upload_handle(user_id="u1", job_id=2, payload=payload)
    assert skipped_flags == [False, True]


def test_ingest_upload_handler_registered() -> None:
    from services import batch_handlers as _batch_handlers  # noqa: F401

    assert "ingest_upload" in batch_queue._handlers
