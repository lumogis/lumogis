# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Read-only DR backup status for admin diagnostics (LUM-185)."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path

from models.api_v1 import AdminDiagnosticsWarning
from models.api_v1 import BackupCoverageItem
from models.api_v1 import BackupStatusResponse
from models.api_v1 import BackupStatusStoreItem

import config
from services import media_storage
from services import sql_like

_log = logging.getLogger(__name__)

# The fixed compose mount target of BACKUP_HOST_DIR (docker-compose.yml). It does
# not follow WORKSPACE_PATH: where the backups are and where the workspace is are
# two different facts (LUM-892).
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


_FILES_SCHEMA1_REASON = "snapshot made before file backup"


def _files_store_item(manifest: dict | None, raw: object) -> BackupStatusStoreItem:
    """The ``files`` store (manifest schema 2, LUM-892)."""
    if isinstance(raw, dict) and raw.get("dir"):
        count = raw.get("file_count")
        size = raw.get("bytes")
        return BackupStatusStoreItem(
            id="files",
            present=True,
            file_count=count if isinstance(count, int) else None,
            bytes=size if isinstance(size, int) else None,
        )
    if isinstance(raw, dict) and raw.get("status") == "failed":
        return BackupStatusStoreItem(
            id="files",
            present=False,
            skip_reason=f"file copy failed (rsync exit {raw.get('rsync_exit', '?')}); "
            "see the backup log",
        )
    reason = None
    if manifest is not None and manifest.get("schema_version") == 1:
        reason = _FILES_SCHEMA1_REASON
    return BackupStatusStoreItem(id="files", present=False, skip_reason=reason)


def _store_items(manifest: dict | None) -> list[BackupStatusStoreItem]:
    stores = (manifest or {}).get("stores") or {}
    items: list[BackupStatusStoreItem] = []
    for store_id in ("postgres", "qdrant", "falkordb", "files"):
        raw = stores.get(store_id)
        if store_id == "files":
            items.append(_files_store_item(manifest, raw))
            continue
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
    # new_bytes = the disk this snapshot actually uses; the rest is hard-linked
    # from earlier snapshots. The Web "Size" stays the on-disk cost (LUM-892).
    files_store = stores.get("files") or {}
    if isinstance(files_store.get("new_bytes"), int):
        total += int(files_store["new_bytes"])
    return total


# ---------------------------------------------------------------------------
# What the backup covers (LUM-892)
# ---------------------------------------------------------------------------

_NO_VERIFIED = "no verified backup yet"


def _under(path: Path, ws: Path) -> bool:
    return path.resolve(strict=False).is_relative_to(ws)


def _coverage(
    manifest: dict | None, stores: list[BackupStatusStoreItem]
) -> list[BackupCoverageItem]:
    """Fixed, ordered list of what the DR backup does and does not hold."""
    by_id = {s.id: s for s in stores}
    ws = config.get_workspace_path()
    raw_files = ((manifest or {}).get("stores") or {}).get("files")
    files_ok = isinstance(raw_files, dict) and bool(raw_files.get("dir"))
    files_failed = isinstance(raw_files, dict) and raw_files.get("status") == "failed"

    def db_item(item_id: str, label: str, store: BackupStatusStoreItem) -> BackupCoverageItem:
        note = None
        if manifest is None:
            note = _NO_VERIFIED
        elif store.skipped:
            note = "graph is off, nothing to back up"
        return BackupCoverageItem(
            id=item_id,  # type: ignore[arg-type]
            label=label,
            covered=store.present,
            note=note,
        )

    def tree_item(item_id: str, label: str, path: Path) -> BackupCoverageItem:
        if not _under(path, ws):
            return BackupCoverageItem(
                id=item_id,  # type: ignore[arg-type]
                label=label,
                covered=False,
                note=f"stored outside the workspace at {path}; back it up yourself",
            )
        if files_ok:
            return BackupCoverageItem(
                id=item_id,  # type: ignore[arg-type]
                label=label,
                covered=True,
            )
        note = (
            "the latest backup could not copy them; see the backup log"
            if files_failed
            else "the next backup includes them"
        )
        return BackupCoverageItem(
            id=item_id,  # type: ignore[arg-type]
            label=label,
            covered=False,
            note=note,
        )

    return [
        db_item("database", "Database (accounts, settings, document index)", by_id["postgres"]),
        db_item("search_index", "Search index", by_id["qdrant"]),
        db_item("knowledge_graph", "Knowledge graph", by_id["falkordb"]),
        tree_item("uploads", "Uploaded documents", config.resolve_uploads_path()),
        tree_item("inbox", "Inbox documents", config.get_inbox_path()),
        tree_item("captures", "Capture photos and voice notes", media_storage.capture_media_root()),
        BackupCoverageItem(
            id="library_folder",
            label="Your library folder",
            covered=False,
            note="your own folder; back it up with your usual tools",
        ),
        BackupCoverageItem(
            id="env_file",
            label="The .env settings file",
            covered=False,
            note="holds the key that decrypts saved passwords; keep a copy somewhere safe",
        ),
        BackupCoverageItem(
            id="models",
            label="Downloaded AI models",
            covered=False,
            note="downloaded again when needed",
        ),
        # In the workspace since LUM-922; it used to be lost on every recreate.
        tree_item("stop_entities", "Stop-entities list", Path(config.get_stop_entities_path())),
    ]


# ---------------------------------------------------------------------------
# Files that are indexed but not on disk (LUM-892)
# ---------------------------------------------------------------------------

_MISSING_TTL_SECONDS = 300.0
_MISSING_CACHE: tuple[float, "MissingFiles"] | None = None
_MISSING_LOCK = threading.Lock()
_AREAS = ("uploads", "captures", "inbox", "workspace_other")


@dataclass(frozen=True)
class MissingFiles:
    """``total`` = uploads + captures + workspace_other. The inbox is reported in
    ``by_area`` only: it has no delete handler, so originals a person clears by
    hand stay in ``file_index`` forever and would raise a warning that never
    goes away."""

    total: int
    by_area: dict[str, int]


def clear_missing_files_cache_for_tests() -> None:
    global _MISSING_CACHE
    with _MISSING_LOCK:
        _MISSING_CACHE = None


def _file_roots() -> list[tuple[str, Path]]:
    """``(area, root)`` for every tree a ``file_index.file_path`` can live under.

    All pure resolvers (no mkdir); deduplicated by path, first area wins.
    """
    candidates = [
        ("uploads", config.resolve_uploads_path()),
        ("inbox", config.get_inbox_path()),
        ("workspace_other", config.get_workspace_path()),
    ]
    seen: set[Path] = set()
    roots: list[tuple[str, Path]] = []
    for area, root in candidates:
        if root in seen:
            continue
        seen.add(root)
        roots.append((area, root))
    return roots


def _like_prefix(root: Path) -> str:
    """``LIKE`` pattern for "everything under root"; backslash, % and _ are escaped."""
    return sql_like.escape_like(str(root)).rstrip("/") + "/%"


def _area_of(file_path: str, roots: list[tuple[str, Path]]) -> str | None:
    """The area of the longest root ``file_path`` sits under."""
    best: tuple[int, str] | None = None
    p = Path(file_path)
    for area, root in roots:
        if p.is_relative_to(root):
            depth = len(root.parts)
            if best is None or depth > best[0]:
                best = (depth, area)
    return best[1] if best else None


def _count_missing_uncached() -> MissingFiles:
    ms = config.get_metadata_store()
    by_area = {a: 0 for a in _AREAS}

    # Capture media: each stored attachment must resolve to a file.
    rows = ms.fetch_all(
        "SELECT DISTINCT storage_key FROM capture_attachments WHERE processing_status = 'stored'"
    )
    for row in rows:
        key = row.get("storage_key")
        try:
            present = media_storage.resolve_storage_key_file(str(key)).is_file()
        except (ValueError, OSError):
            present = False
        if not present:
            by_area["captures"] += 1

    # Uploaded and inbox documents: DISTINCT, because a published document has a
    # second file_index row (the shared projection) with the same file_path.
    roots = _file_roots()
    clauses = " OR ".join("file_path LIKE %s ESCAPE '\\'" for _ in roots)
    doc_rows = ms.fetch_all(
        f"SELECT DISTINCT file_path FROM file_index WHERE {clauses}",  # noqa: S608 - placeholders only
        tuple(_like_prefix(root) for _, root in roots),
    )
    for row in doc_rows:
        file_path = row.get("file_path")
        if not isinstance(file_path, str):
            continue
        if Path(file_path).is_file():
            continue
        area = _area_of(file_path, roots)
        if area is not None:
            by_area[area] += 1

    total = by_area["uploads"] + by_area["captures"] + by_area["workspace_other"]
    return MissingFiles(total=total, by_area=by_area)


def count_missing_files() -> MissingFiles | None:
    """Indexed files that are not on disk, cached for 5 minutes.

    ``None`` when the database cannot be read (nothing is cached then).
    """
    global _MISSING_CACHE
    now = time.monotonic()
    with _MISSING_LOCK:
        if _MISSING_CACHE is not None and now - _MISSING_CACHE[0] < _MISSING_TTL_SECONDS:
            return _MISSING_CACHE[1]
    started = time.monotonic()
    try:
        result = _count_missing_uncached()
    except Exception as exc:  # noqa: BLE001 - status must never fail on a DB hiccup
        _log.warning("backup_status: missing-file count failed (%s)", exc.__class__.__name__)
        return None
    _log.debug("backup_status: missing-file count took %.2fs", time.monotonic() - started)
    with _MISSING_LOCK:
        _MISSING_CACHE = (time.monotonic(), result)
    return result


def _files_warnings(
    manifest: dict | None, missing: MissingFiles | None
) -> list[AdminDiagnosticsWarning]:
    warnings: list[AdminDiagnosticsWarning] = []
    if missing is not None and missing.total > 0:
        breakdown = ", ".join(
            f"{area} {missing.by_area[area]}"
            for area in ("uploads", "captures", "workspace_other")
            if missing.by_area.get(area)
        )
        warnings.append(
            AdminDiagnosticsWarning(
                code="backup_files_missing",
                message=(
                    f"{missing.total} uploaded documents or capture files are not on disk "
                    f"({breakdown}). If you did not delete them, restore them from a backup."
                ),
            )
        )
    if manifest is not None:
        raw = (manifest.get("stores") or {}).get("files")
        if isinstance(raw, dict) and raw.get("status") == "failed":
            warnings.append(
                AdminDiagnosticsWarning(
                    code="backup_files_not_in_snapshot",
                    message=(
                        "The latest backup could not copy uploaded documents or captures "
                        f"(rsync exit {raw.get('rsync_exit', '?')}). Its databases are saved. "
                        "See the backup log."
                    ),
                )
            )
        elif not isinstance(raw, dict):
            warnings.append(
                AdminDiagnosticsWarning(
                    code="backup_files_not_in_snapshot",
                    message=(
                        "The latest backup has no copy of uploaded documents or captures. "
                        "Run make update (or docker compose build backup, then "
                        "docker compose up -d backup) and then make backup."
                    ),
                )
            )
    return warnings


def build_backup_status_response() -> BackupStatusResponse:
    enabled = os.environ.get("BACKUP_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    stale_hours = float(os.environ.get("BACKUP_STALE_HOURS", "24"))
    warnings: list[AdminDiagnosticsWarning] = []
    missing = count_missing_files()
    missing_total = missing.total if missing is not None else None
    missing_by_area = dict(missing.by_area) if missing is not None else None

    if not _SNAPSHOTS_DIR.is_dir():
        if enabled:
            warnings.append(
                AdminDiagnosticsWarning(
                    code="backup_snapshots_missing",
                    message="No DR snapshots directory yet — run make backup",
                )
            )
        warnings.extend(_files_warnings(None, missing))
        stores = _store_items(None)
        return BackupStatusResponse(
            enabled=enabled,
            backup_dir=str(_BACKUP_ROOT),
            stale_threshold_hours=stale_hours,
            stores=stores,
            warnings=warnings,
            coverage=_coverage(None, stores),
            files_missing=missing_total,
            files_missing_by_area=missing_by_area,
        )

    snap_dir, manifest = _pick_latest_snapshot()
    if manifest is None:
        warnings.append(
            AdminDiagnosticsWarning(
                code="backup_no_verified_snapshot",
                message="No verified DR snapshot found — run make backup",
            )
        )
        warnings.extend(_files_warnings(None, missing))
        stores = _store_items(None)
        return BackupStatusResponse(
            enabled=enabled,
            backup_dir=str(_BACKUP_ROOT),
            stale_threshold_hours=stale_hours,
            stores=stores,
            last_verify_status="unknown",
            warnings=warnings,
            coverage=_coverage(None, stores),
            files_missing=missing_total,
            files_missing_by_area=missing_by_area,
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
    warnings.extend(_files_warnings(manifest, missing))

    verify_status = manifest.get("verify_status")
    last_verify: str | None
    if verify_status in ("ok", "failed"):
        last_verify = verify_status
    else:
        last_verify = "unknown"

    stores = _store_items(manifest)
    return BackupStatusResponse(
        enabled=enabled,
        backup_dir=str(_BACKUP_ROOT),
        last_snapshot_id=snap_dir.name if snap_dir else None,
        last_success_at=created_at if isinstance(created_at, str) else None,
        age_hours=age_hours,
        stale=stale,
        stale_threshold_hours=stale_hours,
        total_bytes=_total_bytes(manifest, snap_dir),
        stores=stores,
        last_verify_status=last_verify,
        warnings=warnings,
        coverage=_coverage(manifest, stores),
        files_missing=missing_total,
        files_missing_by_area=missing_by_area,
    )
