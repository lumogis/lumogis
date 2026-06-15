# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Bundled Hub bootstrap: library scan progress for ``GET /healthz``."""

from __future__ import annotations

import logging
import threading
from enum import Enum

import config

_log = logging.getLogger(__name__)

_lock = threading.Lock()
_scan_phase: str = "idle"
_scan_total: int = 0
_scan_done: int = 0


class IndexScanPhase(str, Enum):
    IDLE = "idle"
    QUEUED = "queued"
    SCANNING = "scanning"
    READY = "ready"


def reset_scan_state() -> None:
    """Reset scan progress back to ``idle`` (used before a fresh scan / in tests)."""
    with _lock:
        global _scan_phase, _scan_total, _scan_done
        _scan_phase = IndexScanPhase.IDLE.value
        _scan_total = 0
        _scan_done = 0


def mark_scan_queued() -> None:
    with _lock:
        global _scan_phase
        if _scan_phase in (IndexScanPhase.IDLE.value, IndexScanPhase.READY.value):
            _scan_phase = IndexScanPhase.QUEUED.value


def begin_folder_scan(*, total_files: int) -> None:
    with _lock:
        global _scan_phase, _scan_total, _scan_done
        _scan_phase = IndexScanPhase.SCANNING.value
        _scan_total = max(0, total_files)
        _scan_done = 0
    _log.info("Index folder scan started (%d supported files)", total_files)


def report_folder_scan_progress(*, done: int, total: int | None = None) -> None:
    with _lock:
        global _scan_done, _scan_total
        _scan_done = max(0, done)
        if total is not None:
            _scan_total = max(0, total)


def complete_folder_scan(*, total_files: int, ingested: int, skipped: int, errors: int) -> None:
    with _lock:
        global _scan_phase, _scan_total, _scan_done
        _scan_phase = IndexScanPhase.READY.value
        _scan_total = max(0, total_files)
        _scan_done = _scan_total
    _log.info(
        "Index folder scan complete (total=%d ingested=%d skipped=%d errors=%d)",
        total_files,
        ingested,
        skipped,
        errors,
    )


def _file_index_count() -> int:
    owner = config.get_ingest_paths_owner_user_id()
    if not owner:
        return 0
    try:
        ms = config.get_metadata_store()
        # SCOPE-EXEMPT: file_index is per-user ingest bookkeeping (no scope column).
        row = ms.fetch_one(
            "SELECT COUNT(*) AS n FROM file_index WHERE user_id = %s",
            (owner,),
        )
        return int(row["n"] or 0) if row else 0
    except Exception:
        _log.debug("file_index count unavailable", exc_info=True)
        return 0


def prior_library_index_exists() -> bool:
    """True when ``file_index`` already has rows for the ingest-path owner.

    Used by bundled Hub cold start: ``LUMOGIS_DEFER_LIBRARY_INDEX`` must still
    block the first wizard scan, but after onboarding a Core restart should
    resync files added while the app was fully quit (watchers only see live
    events).
    """
    return _file_index_count() > 0


def index_bootstrap_status() -> dict[str, str]:
    """Public liveness subset for ``GET /healthz`` (string values only)."""
    with _lock:
        phase = _scan_phase
        scan_total = _scan_total
        scan_done = _scan_done
    indexed = _file_index_count()
    return {
        "index_scan": phase,
        "index_scan_total": str(scan_total),
        "index_scan_done": str(scan_done),
        "index_file_count": str(indexed),
    }
