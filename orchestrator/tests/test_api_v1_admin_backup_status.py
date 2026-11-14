# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``GET /api/v1/admin/diagnostics/backup-status`` — DR backup status API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from models.api_v1 import BackupStatusResponse


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


def _auth_header(
    monkeypatch: pytest.MonkeyPatch, user_id: str, role: str = "admin"
) -> dict[str, str]:
    monkeypatch.setenv("AUTH_SECRET", "test-backup-status-secret-do-not-use")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    from auth import mint_access_token

    tok = mint_access_token(user_id, role)
    return {"Authorization": f"Bearer {tok}"}


def test_backup_status_403_non_admin(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "bob", "user")
    r = client.get("/api/v1/admin/diagnostics/backup-status", headers=hdr)
    assert r.status_code == 403


def test_backup_status_401_unauthenticated(client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_SECRET", "test-backup-status-secret-do-not-use")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    r = client.get("/api/v1/admin/diagnostics/backup-status")
    assert r.status_code == 401


def test_backup_status_200_admin_contract(client, monkeypatch) -> None:
    from services import backup_status as backup_status_svc

    sample = BackupStatusResponse(
        enabled=True,
        backup_dir="/workspace/backups",
        last_snapshot_id="20260102-030000",
        last_success_at="2026-01-02T03:00:00Z",
        age_hours=1.5,
        stale=False,
        stale_threshold_hours=24.0,
        total_bytes=1024,
        stores=[
            {"id": "postgres", "present": True, "skipped": False},
            {"id": "qdrant", "present": True, "skipped": False},
            {"id": "falkordb", "present": False, "skipped": True, "skip_reason": "graph disabled"},
            {"id": "files", "present": True, "file_count": 12, "bytes": 2048},
        ],
        last_verify_status="ok",
        warnings=[],
        coverage=[
            {"id": "database", "label": "Database", "covered": True},
            {"id": "uploads", "label": "Uploaded documents", "covered": True},
            {"id": "env_file", "label": "The .env settings file", "covered": False, "note": "n"},
        ],
        files_missing=0,
        files_missing_by_area={"uploads": 0, "captures": 0, "inbox": 0, "workspace_other": 0},
    )

    monkeypatch.setattr(
        backup_status_svc,
        "build_backup_status_response",
        lambda: sample,
    )
    hdr = _auth_header(monkeypatch, "admin-1", "admin")
    r = client.get("/api/v1/admin/diagnostics/backup-status", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    assert body["last_snapshot_id"] == "20260102-030000"
    assert body["stores"][2]["skipped"] is True
    assert body["stores"][3]["id"] == "files"
    assert body["stores"][3]["file_count"] == 12
    assert isinstance(body["coverage"], list)
    assert body["coverage"][2]["covered"] is False
    assert body["files_missing"] == 0
    assert body["files_missing_by_area"]["captures"] == 0


def test_backup_status_real_service_returns_ten_coverage_items(
    client, monkeypatch, tmp_path
) -> None:
    """No stubbing: the real service on an empty backup root (DB mocked by conftest)."""
    from services import backup_status as backup_status_svc

    monkeypatch.setattr(backup_status_svc, "_SNAPSHOTS_DIR", tmp_path / "none")
    backup_status_svc.clear_missing_files_cache_for_tests()
    hdr = _auth_header(monkeypatch, "admin-1", "admin")
    r = client.get("/api/v1/admin/diagnostics/backup-status", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    assert len(body["coverage"]) == 10
    assert "files_missing" in body
    assert body["files_missing"] is None or isinstance(body["files_missing"], int)
    backup_status_svc.clear_missing_files_cache_for_tests()
