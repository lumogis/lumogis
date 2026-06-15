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
        ],
        last_verify_status="ok",
        warnings=[],
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
