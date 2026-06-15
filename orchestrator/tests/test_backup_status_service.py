# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for backup_status service (LUM-185)."""

from __future__ import annotations

import json
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

import pytest

from services import backup_status as svc


def _write_snapshot(root: Path, snap_id: str, *, verify_status: str, created_at: str) -> None:
    snap = root / snap_id
    snap.mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "created_at": created_at,
        "verify_status": verify_status,
        "stores": {
            "postgres": {"file": "postgres.dump", "bytes": 10},
            "qdrant": {"collections": [], "files": {}},
            "falkordb": {"skipped": True},
        },
    }
    (snap / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (snap / "postgres.dump").write_bytes(b"pg")


def test_backup_status_reads_latest_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()
    _write_snapshot(snap_root, "20260101-030000", verify_status="ok", created_at="2026-01-01T03:00:00Z")
    _write_snapshot(snap_root, "20260102-030000", verify_status="ok", created_at="2026-01-02T03:00:00Z")

    monkeypatch.setattr(svc, "_SNAPSHOTS_DIR", snap_root)
    monkeypatch.setattr(svc, "_BACKUP_ROOT", tmp_path)

    resp = svc.build_backup_status_response()
    assert resp.last_snapshot_id == "20260102-030000"
    assert resp.last_verify_status == "ok"


def test_backup_status_stale_when_age_gt_threshold(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()
    old = (datetime.now(timezone.utc) - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_snapshot(snap_root, "20260101-030000", verify_status="ok", created_at=old)

    monkeypatch.setattr(svc, "_SNAPSHOTS_DIR", snap_root)
    monkeypatch.setattr(svc, "_BACKUP_ROOT", tmp_path)
    monkeypatch.setenv("BACKUP_STALE_HOURS", "24")

    resp = svc.build_backup_status_response()
    assert resp.stale is True
    assert resp.age_hours is not None
    assert resp.age_hours > 24


def test_backup_status_falkordb_skipped_when_graph_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()
    _write_snapshot(snap_root, "20260102-030000", verify_status="ok", created_at="2026-01-02T03:00:00Z")

    monkeypatch.setattr(svc, "_SNAPSHOTS_DIR", snap_root)
    monkeypatch.setattr(svc, "_BACKUP_ROOT", tmp_path)

    resp = svc.build_backup_status_response()
    falkor = next(s for s in resp.stores if s.id == "falkordb")
    assert falkor.skipped is True
    assert falkor.present is False
