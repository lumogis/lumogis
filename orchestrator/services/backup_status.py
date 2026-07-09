# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Read-only DR backup status for admin diagnostics (LUM-185)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from datetime import timezone
from pathlib import Path

from models.api_v1 import AdminDiagnosticsWarning
from models.api_v1 import BackupStatusResponse
from models.api_v1 import BackupStatusStoreItem

_log = logging.getLogger(__name__)

_BACKUP_ROOT = Path("/workspace/backups")
_SNAPSHOTS_DIR = _BACKUP_ROOT / "snapshots"


def _parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _load_manifest(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning("backup_status: manifest read failed %s (%s)", path, exc.__class__.__name__)
        return None


def _snapshot_sort_key(manifest: dict, snap_dir: Path) -> tuple[str, float]:
    created = manifest.get("created_at")
    if isinstance(created, str):
        return (created, snap_dir.stat().st_mtime)
    return (snap_dir.name, snap_dir.stat().st_mtime)


def _pick_latest_snapshot() -> tuple[Path | None, dict | None]:
    if not _SNAPSHOTS_DIR.is_dir():
        return None, None
    candidates: list[tuple[Path, dict]] = []
    for child in _SNAPSHOTS_DIR.iterdir():
        if not child.is_dir() or child.name == ".tmp":
            continue
        manifest_path = child / "manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = _load_manifest(manifest_path)
        if manifest is None:
            continue
        if manifest.get("verify_status") == "ok":
            candidates.append((child, manifest))
    if not candidates:
        return None, None
    candidates.sort(key=lambda item: _snapshot_sort_key(item[1], item[0]), reverse=True)
    snap_dir, manifest = candidates[0]
    return snap_dir, manifest


def _store_items(manifest: dict | None) -> list[BackupStatusStoreItem]:
    stores = (manifest or {}).get("stores") or {}
    items: list[BackupStatusStoreItem] = []
    for store_id in ("postgres", "qdrant", "falkordb"):
        raw = stores.get(store_id)
        if not isinstance(raw, dict):
            items.append(
                BackupStatusStoreItem(
                    id=store_id,  # type: ignore[arg-type]
                    present=False,
                )
            )
            continue
        skipped = bool(raw.get("skipped"))
        if store_id == "qdrant":
            present = not skipped and bool(raw.get("collections") or raw.get("files"))
        else:
            present = not skipped and bool(raw.get("file"))
        skip_reason = None
        if skipped:
            skip_reason = "graph disabled or BACKUP_INCLUDE_FALKORDB=false"
        items.append(
            BackupStatusStoreItem(
                id=store_id,  # type: ignore[arg-type]
                present=present,
                skipped=skipped,
                skip_reason=skip_reason,
            )
        )
    return items


def _total_bytes(manifest: dict | None, snap_dir: Path | None) -> int | None:
    if manifest is None or snap_dir is None:
        return None
    total = 0
    stores = manifest.get("stores") or {}
    pg = stores.get("postgres") or {}
    if isinstance(pg.get("bytes"), int):
        total += int(pg["bytes"])
    qdrant = stores.get("qdrant") or {}
    files = qdrant.get("files") or {}
    if isinstance(files, dict):
        for rel in files.values():
            if not isinstance(rel, str):
                continue
            path = snap_dir / rel
            if path.is_file():
                total += path.stat().st_size
    falkor = stores.get("falkordb") or {}
    if not falkor.get("skipped") and isinstance(falkor.get("file"), str):
        path = snap_dir / str(falkor["file"])
        if path.is_file():
            total += path.stat().st_size
    return total


def build_backup_status_response() -> BackupStatusResponse:
    enabled = os.environ.get("BACKUP_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    stale_hours = float(os.environ.get("BACKUP_STALE_HOURS", "24"))
    warnings: list[AdminDiagnosticsWarning] = []

    if not _SNAPSHOTS_DIR.is_dir():
        if enabled:
            warnings.append(
                AdminDiagnosticsWarning(
                    code="backup_snapshots_missing",
                    message="No DR snapshots directory yet — run make backup",
                )
            )
        return BackupStatusResponse(
            enabled=enabled,
            backup_dir=str(_BACKUP_ROOT),
            stale_threshold_hours=stale_hours,
            stores=_store_items(None),
            warnings=warnings,
        )

    snap_dir, manifest = _pick_latest_snapshot()
    if manifest is None:
        warnings.append(
            AdminDiagnosticsWarning(
                code="backup_no_verified_snapshot",
                message="No verified DR snapshot found — run make backup",
            )
        )
        return BackupStatusResponse(
            enabled=enabled,
            backup_dir=str(_BACKUP_ROOT),
            stale_threshold_hours=stale_hours,
            stores=_store_items(None),
            last_verify_status="unknown",
            warnings=warnings,
        )

    created_at = manifest.get("created_at")
    created_dt = _parse_iso8601(created_at if isinstance(created_at, str) else None)
    age_hours: float | None = None
    stale = False
    if created_dt is not None:
        age_hours = (
            datetime.now(timezone.utc) - created_dt.astimezone(timezone.utc)
        ).total_seconds() / 3600.0
        stale = age_hours > stale_hours
        if stale:
            warnings.append(
                AdminDiagnosticsWarning(
                    code="backup_stale",
                    message=(
                        f"Last verified backup is {age_hours:.1f}h old "
                        f"(threshold {stale_hours:.0f}h)"
                    ),
                )
            )

    verify_status = manifest.get("verify_status")
    last_verify: str | None
    if verify_status in ("ok", "failed"):
        last_verify = verify_status
    else:
        last_verify = "unknown"

    return BackupStatusResponse(
        enabled=enabled,
        backup_dir=str(_BACKUP_ROOT),
        last_snapshot_id=snap_dir.name if snap_dir else None,
        last_success_at=created_at if isinstance(created_at, str) else None,
        age_hours=age_hours,
        stale=stale,
        stale_threshold_hours=stale_hours,
        total_bytes=_total_bytes(manifest, snap_dir),
        stores=_store_items(manifest),
        last_verify_status=last_verify,
        warnings=warnings,
    )
