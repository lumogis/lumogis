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


def test_backup_status_reads_latest_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()
    _write_snapshot(
        snap_root, "20260101-030000", verify_status="ok", created_at="2026-01-01T03:00:00Z"
    )
    _write_snapshot(
        snap_root, "20260102-030000", verify_status="ok", created_at="2026-01-02T03:00:00Z"
    )

    monkeypatch.setattr(svc, "_SNAPSHOTS_DIR", snap_root)
    monkeypatch.setattr(svc, "_BACKUP_ROOT", tmp_path)

    resp = svc.build_backup_status_response()
    assert resp.last_snapshot_id == "20260102-030000"
    assert resp.last_verify_status == "ok"


def test_backup_status_stale_when_age_gt_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    _write_snapshot(
        snap_root, "20260102-030000", verify_status="ok", created_at="2026-01-02T03:00:00Z"
    )

    monkeypatch.setattr(svc, "_SNAPSHOTS_DIR", snap_root)
    monkeypatch.setattr(svc, "_BACKUP_ROOT", tmp_path)

    resp = svc.build_backup_status_response()
    falkor = next(s for s in resp.stores if s.id == "falkordb")
    assert falkor.skipped is True
    assert falkor.present is False


# ---------------------------------------------------------------------------
# LUM-892 — files store, coverage list, missing-file count
# ---------------------------------------------------------------------------

import re  # noqa: E402

import config  # noqa: E402
from services import media_storage  # noqa: E402

_COVERAGE_IDS = [
    "database",
    "search_index",
    "knowledge_graph",
    "uploads",
    "inbox",
    "captures",
    "library_folder",
    "env_file",
    "models",
    "stop_entities",
]


def _write_v2_snapshot(
    root: Path,
    snap_id: str = "20260102-030000",
    *,
    files: dict | None = None,
    schema_version: int = 2,
    falkor_skipped: bool = True,
) -> None:
    snap = root / snap_id
    snap.mkdir(parents=True)
    stores: dict = {
        "postgres": {"file": "postgres.dump", "bytes": 100},
        "qdrant": {"collections": ["c"], "files": {}},
        "falkordb": {"skipped": falkor_skipped},
    }
    if files is not None:
        stores["files"] = files
    (snap / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": schema_version,
                "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "verify_status": "ok",
                "stores": stores,
            }
        ),
        encoding="utf-8",
    )
    (snap / "postgres.dump").write_bytes(b"pg")


_FILES_OK = {
    "dir": "files/workspace",
    "file_count": 7,
    "bytes": 1000,
    "new_bytes": 10,
    "status": "ok",
}


class _FakeStore:
    """Stands in for the metadata store; emulates the two queries the status runs.

    The ``file_index`` query is a ``LIKE`` per root; the fake applies the same
    prefixes (unescaped) so a row outside every root is filtered the way SQL would.
    """

    def __init__(self, attachment_keys=(), file_paths=(), fail: bool = False):
        self.attachment_keys = list(attachment_keys)
        self.file_paths = list(file_paths)
        self.fail = fail
        self.queries: list[tuple[str, tuple | None]] = []

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        self.queries.append((query, params))
        if self.fail:
            raise RuntimeError("database unavailable")
        if "capture_attachments" in query:
            assert "processing_status = 'stored'" in query
            return [{"storage_key": k} for k in self.attachment_keys]
        assert "FROM file_index" in query
        prefixes = [re.sub(r"\\(.)", r"\1", p)[:-1] for p in (params or ())]  # drop trailing '%'
        rows = {fp for fp in self.file_paths if any(fp.startswith(pre) for pre in prefixes)}
        return [{"file_path": fp} for fp in sorted(rows)]


@pytest.fixture
def ws(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Workspace + backup root under tmp_path, with a fake metadata store."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()
    for name in (
        "LUMOGIS_UPLOADS_PATH",
        "LUMOGIS_INBOX_PATH",
        "CAPTURE_MEDIA_ROOT",
        "STOP_ENTITIES_PATH",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WORKSPACE_PATH", str(workspace))
    monkeypatch.setenv("LUMOGIS_INBOX_PATH", str(workspace / "inbox"))
    monkeypatch.setattr(svc, "_SNAPSHOTS_DIR", snap_root)
    monkeypatch.setattr(svc, "_BACKUP_ROOT", tmp_path)
    monkeypatch.setattr(media_storage, "_CAPTURE_MEDIA_ROOT", workspace.resolve() / "captures")
    store = _FakeStore()
    monkeypatch.setitem(config._instances, "metadata_store", store)
    svc.clear_missing_files_cache_for_tests()
    yield type("Ws", (), {"root": workspace.resolve(), "snaps": snap_root, "store": store})
    svc.clear_missing_files_cache_for_tests()


def _coverage_by_id(resp):
    return {c.id: c for c in resp.coverage}


def test_files_store_present_in_v2_manifest(ws) -> None:
    _write_v2_snapshot(ws.snaps, files=_FILES_OK)
    resp = svc.build_backup_status_response()
    item = next(s for s in resp.stores if s.id == "files")
    assert item.present is True
    assert item.file_count == 7
    assert item.bytes == 1000
    assert "backup_files_not_in_snapshot" not in {w.code for w in resp.warnings}


def test_v1_manifest_files_store_absent_with_reason(ws) -> None:
    _write_snapshot(ws.snaps, "20260102-030000", verify_status="ok", created_at=_now_iso())
    resp = svc.build_backup_status_response()
    item = next(s for s in resp.stores if s.id == "files")
    assert item.present is False
    assert item.skipped is False
    assert item.skip_reason == "snapshot made before file backup"
    warn = next(w for w in resp.warnings if w.code == "backup_files_not_in_snapshot")
    assert "docker compose build backup" in warn.message


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_total_bytes_counts_new_bytes_not_logical(ws) -> None:
    _write_v2_snapshot(ws.snaps, files={**_FILES_OK, "bytes": 1000, "new_bytes": 10})
    resp = svc.build_backup_status_response()
    # postgres.bytes (100) + new_bytes (10): the on-disk cost, not the logical 1000
    assert resp.total_bytes == 110


def test_files_store_failed_status(ws) -> None:
    _write_v2_snapshot(ws.snaps, files={"status": "failed", "rsync_exit": 23})
    resp = svc.build_backup_status_response()
    item = next(s for s in resp.stores if s.id == "files")
    assert item.present is False
    assert "rsync exit 23" in (item.skip_reason or "")
    warn = next(w for w in resp.warnings if w.code == "backup_files_not_in_snapshot")
    assert "databases are saved" in warn.message
    cov = _coverage_by_id(resp)
    assert not any(cov[i].covered for i in ("uploads", "inbox", "captures"))


def test_coverage_lists_fixed_ids_in_order(ws) -> None:
    _write_v2_snapshot(ws.snaps, files=_FILES_OK)
    resp = svc.build_backup_status_response()
    assert [c.id for c in resp.coverage] == _COVERAGE_IDS


def test_coverage_files_covered_only_with_files_store(ws) -> None:
    _write_v2_snapshot(ws.snaps, files=_FILES_OK)
    cov = _coverage_by_id(svc.build_backup_status_response())
    assert all(cov[i].covered for i in ("uploads", "inbox", "captures", "stop_entities"))
    assert cov["database"].covered is True
    for not_covered in ("library_folder", "env_file", "models"):
        assert cov[not_covered].covered is False

    import shutil

    shutil.rmtree(ws.snaps / "20260102-030000")
    _write_snapshot(ws.snaps, "20260102-030000", verify_status="ok", created_at=_now_iso())
    cov = _coverage_by_id(svc.build_backup_status_response())
    for i in ("uploads", "inbox", "captures", "stop_entities"):
        assert cov[i].covered is False
        assert "next backup" in (cov[i].note or "")


def test_coverage_capture_root_outside_workspace_not_covered(
    ws, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere" / "captures"
    monkeypatch.setattr(media_storage, "_CAPTURE_MEDIA_ROOT", outside)
    _write_v2_snapshot(ws.snaps, files=_FILES_OK)
    cov = _coverage_by_id(svc.build_backup_status_response())
    assert cov["captures"].covered is False
    assert str(outside) in (cov["captures"].note or "")
    assert cov["uploads"].covered is True


def test_coverage_stop_entities_in_workspace_is_covered(ws) -> None:
    """LUM-922: the list lives in <workspace>/config, which the DR backup copies."""
    _write_v2_snapshot(ws.snaps, files=_FILES_OK)
    cov = _coverage_by_id(svc.build_backup_status_response())
    assert cov["stop_entities"].covered is True
    assert cov["stop_entities"].note is None
    assert config.get_stop_entities_path() == str(ws.root / "config" / "stop_entities.txt")


def test_coverage_stop_entities_outside_workspace_not_covered(
    ws, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere" / "stop_entities.txt"
    monkeypatch.setenv("STOP_ENTITIES_PATH", str(outside))
    _write_v2_snapshot(ws.snaps, files=_FILES_OK)
    cov = _coverage_by_id(svc.build_backup_status_response())
    assert cov["stop_entities"].covered is False
    assert str(outside) in (cov["stop_entities"].note or "")


def test_coverage_uploads_outside_workspace_not_covered(
    ws, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LUMOGIS_UPLOADS_PATH", str(tmp_path / "elsewhere"))
    _write_v2_snapshot(ws.snaps, files=_FILES_OK)
    cov = _coverage_by_id(svc.build_backup_status_response())
    assert cov["uploads"].covered is False
    assert "outside the workspace" in (cov["uploads"].note or "")


def test_coverage_graph_skipped_note(ws) -> None:
    _write_v2_snapshot(ws.snaps, files=_FILES_OK, falkor_skipped=True)
    cov = _coverage_by_id(svc.build_backup_status_response())
    assert cov["knowledge_graph"].covered is False
    assert "graph is off" in (cov["knowledge_graph"].note or "")


def test_coverage_no_snapshot_dir(ws, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(svc, "_SNAPSHOTS_DIR", tmp_path / "does-not-exist")
    resp = svc.build_backup_status_response()
    assert [c.id for c in resp.coverage] == _COVERAGE_IDS
    cov = _coverage_by_id(resp)
    for i in ("database", "search_index", "knowledge_graph"):
        assert cov[i].covered is False
        assert cov[i].note == "no verified backup yet"


def test_status_read_creates_no_uploads_dir(ws) -> None:
    """Backup status is a read: it must not mkdir the uploads dir."""
    assert not (ws.root / "uploads").exists()
    svc.build_backup_status_response()
    assert not (ws.root / "uploads").exists()


# ── missing-file count ──────────────────────────────────────────────────────


def _touch(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return str(path)


def test_missing_files_counts_captures_and_workspace_documents(ws) -> None:
    present_cap = _touch(ws.root / "captures" / "u/c/a1/blob.jpg")
    assert present_cap
    ws.store.attachment_keys = ["u/c/a1/blob.jpg", "u/c/a2/blob.jpg"]  # second is missing
    up_ok = _touch(ws.root / "uploads" / "u" / "1_ok.txt")
    ws.store.file_paths = [
        up_ok,
        str(ws.root / "uploads" / "u" / "2_gone.txt"),
        str(ws.root / "uploads" / "u" / "3_gone.txt"),
        "/data/library/elsewhere.txt",  # outside every root: must not count
    ]
    resp = svc.build_backup_status_response()
    assert resp.files_missing == 3
    warn = next(w for w in resp.warnings if w.code == "backup_files_missing")
    assert warn.message.startswith("3 uploaded documents or capture files are not on disk")
    assert "uploads 2, captures 1" in warn.message


def test_missing_files_like_prefix_escapes_wildcards(
    ws, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    odd = tmp_path / "a_b%c"
    odd.mkdir()
    monkeypatch.setenv("WORKSPACE_PATH", str(odd))
    monkeypatch.setenv("LUMOGIS_INBOX_PATH", str(odd / "inbox"))
    svc.count_missing_files()
    doc_query = next(q for q in ws.store.queries if "FROM file_index" in q[0])
    assert doc_query[1] is not None
    assert f"{odd.resolve()}".replace("_", "\\_").replace("%", "\\%") + "/%" in doc_query[1]
    assert "ESCAPE '\\'" in doc_query[0]


def test_missing_files_counts_shared_projection_once(ws) -> None:
    gone = str(ws.root / "uploads" / "u" / "1_gone.txt")
    # personal row + shared projection row: same file_path (the fake returns a set,
    # like SELECT DISTINCT does)
    ws.store.file_paths = [gone, gone]
    result = svc.count_missing_files()
    assert result is not None and result.total == 1
    doc_query = next(q for q in ws.store.queries if "FROM file_index" in q[0])
    assert "DISTINCT file_path" in doc_query[0]


def test_missing_files_db_error_returns_none_and_no_warning(ws) -> None:
    _write_v2_snapshot(ws.snaps, files=_FILES_OK)
    ws.store.fail = True
    resp = svc.build_backup_status_response()
    assert resp.files_missing is None
    assert resp.files_missing_by_area is None
    assert "backup_files_missing" not in {w.code for w in resp.warnings}
    calls = len(ws.store.queries)
    svc.build_backup_status_response()
    assert len(ws.store.queries) > calls  # a failure is not cached


def test_missing_files_cached_for_ttl(ws) -> None:
    svc.count_missing_files()
    first = len(ws.store.queries)
    svc.count_missing_files()
    assert len(ws.store.queries) == first
    svc.clear_missing_files_cache_for_tests()
    svc.count_missing_files()
    assert len(ws.store.queries) == 2 * first


def test_missing_files_bad_storage_key_counts_as_missing(ws) -> None:
    ws.store.attachment_keys = ["../../etc/passwd"]
    result = svc.count_missing_files()
    assert result is not None and result.by_area["captures"] == 1


def test_missing_files_by_area_and_inbox_not_in_total(ws) -> None:
    _touch(ws.root / "captures" / "u/c/a1/blob.jpg")
    ws.store.attachment_keys = ["u/c/a1/blob.jpg", "u/c/a2/blob.jpg"]
    ws.store.file_paths = [
        str(ws.root / "uploads" / "u" / "1_gone.txt"),
        str(ws.root / "inbox" / "a.txt"),
        str(ws.root / "inbox" / "b.txt"),
    ]
    resp = svc.build_backup_status_response()
    assert resp.files_missing == 2
    assert resp.files_missing_by_area == {
        "uploads": 1,
        "captures": 1,
        "inbox": 2,
        "workspace_other": 0,
    }
    warn = next(w for w in resp.warnings if w.code == "backup_files_missing")
    assert "uploads 1, captures 1" in warn.message
    assert "inbox" not in warn.message

    svc.clear_missing_files_cache_for_tests()
    ws.store.attachment_keys = []
    ws.store.file_paths = [str(ws.root / "inbox" / "a.txt")]
    resp = svc.build_backup_status_response()
    assert resp.files_missing == 0
    assert "backup_files_missing" not in {w.code for w in resp.warnings}


def test_missing_files_uploads_outside_workspace_checked(
    ws, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setenv("LUMOGIS_UPLOADS_PATH", str(elsewhere))
    ws.store.file_paths = [str(elsewhere.resolve() / "u" / "1_x")]
    result = svc.count_missing_files()
    assert result is not None
    assert result.by_area["uploads"] == 1
    doc_query = next(q for q in ws.store.queries if "FROM file_index" in q[0])
    assert len(doc_query[1] or ()) == 3  # uploads, inbox, workspace


def test_failed_attachments_not_counted(ws) -> None:
    ws.store.attachment_keys = []  # the fake only returns processing_status = 'stored' rows
    svc.count_missing_files()
    cap_query = next(q for q in ws.store.queries if "capture_attachments" in q[0])
    assert "processing_status = 'stored'" in cap_query[0]
