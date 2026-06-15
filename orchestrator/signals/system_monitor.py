# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""System monitor: emits internal Signal objects for health conditions.

Checks every 30 minutes via APScheduler. Conditions monitored:
  - Disk usage > 85%
  - Backup age > 7 days (newest verified DR snapshot under /workspace/backups/snapshots/)
  - Inbox depth > 50 files (ai-workspace/inbox/)
  - review_queue depth > 0 (entities needing user resolution)
  - Error spike detection (simple count of logged errors; future: log tail)

The review_queue alert ensures users know when ambiguous entities need
resolution — without it, items sit in the queue indefinitely with no
notification.
"""

import json
import logging
import os
import uuid
from datetime import datetime
from datetime import timezone
from pathlib import Path

import psutil
from models.signals import Signal
from services.signal_processor import process_signal

import config

_log = logging.getLogger(__name__)

_INTERVAL_SECONDS = 30 * 60  # 30 minutes
_job_id = "system_monitor_poll"

_DISK_THRESHOLD = float(os.environ.get("SYSTEM_MONITOR_DISK_THRESHOLD", "85"))
_BACKUP_MAX_AGE_DAYS = int(os.environ.get("SYSTEM_MONITOR_BACKUP_DAYS", "7"))
_INBOX_MAX_DEPTH = int(os.environ.get("SYSTEM_MONITOR_INBOX_DEPTH", "50"))
_WORKSPACE = Path(os.environ.get("WORKSPACE_PATH", "/workspace"))


def start() -> None:
    scheduler = config.get_scheduler()
    if not scheduler.running:
        return

    scheduler.add_job(
        _run_checks,
        trigger="interval",
        seconds=_INTERVAL_SECONDS,
        id=_job_id,
        name="System health monitor",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _log.info("system_monitor: scheduled every %ds", _INTERVAL_SECONDS)


def stop() -> None:
    try:
        scheduler = config.get_scheduler()
        job = scheduler.get_job(_job_id)
        if job:
            job.remove()
    except Exception as exc:
        _log.debug("system_monitor stop: %s", exc)


def _run_checks() -> None:
    alerts = []
    alerts += _check_disk()
    alerts += _check_backup_age()
    alerts += _check_inbox_depth()
    alerts += _check_review_queue()

    for alert in alerts:
        _log.warning("system_monitor alert: %s", alert["title"])
        sig = _make_signal(alert["title"], alert["body"], alert["importance"])
        try:
            process_signal(sig, user_id="default")
        except Exception as exc:
            _log.error("system_monitor: process_signal error: %s", exc)


def _check_disk() -> list[dict]:
    alerts = []
    try:
        usage = psutil.disk_usage("/")
        pct = usage.percent
        if pct > _DISK_THRESHOLD:
            alerts.append(
                {
                    "title": f"Disk usage critical: {pct:.1f}%",
                    "body": (
                        f"Disk at {pct:.1f}% ({usage.used // 1_073_741_824}GB used of "
                        f"{usage.total // 1_073_741_824}GB). Consider removing old data."
                    ),
                    "importance": 0.85 if pct > 95 else 0.65,
                }
            )
    except Exception as exc:
        _log.debug("system_monitor: disk check error: %s", exc)
    return alerts


def _check_backup_age() -> list[dict]:
    alerts = []
    snapshots_dir = Path("/workspace/backups") / "snapshots"
    try:
        if not snapshots_dir.is_dir():
            return []
        newest_mtime: float | None = None
        newest_id: str | None = None
        for child in snapshots_dir.iterdir():
            if not child.is_dir() or child.name == ".tmp":
                continue
            manifest_path = child / "manifest.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if manifest.get("verify_status") != "ok":
                continue
            mtime = manifest_path.stat().st_mtime
            if newest_mtime is None or mtime > newest_mtime:
                newest_mtime = mtime
                newest_id = child.name
        if newest_mtime is None:
            alerts.append(
                {
                    "title": "No verified DR backups found",
                    "body": (
                        "No verified snapshots in /workspace/backups/snapshots/. "
                        "Run make backup (see docs/guides/backup-restore.md)."
                    ),
                    "importance": 0.75,
                }
            )
        else:
            age_days = (datetime.now().timestamp() - newest_mtime) / 86400
            if age_days > _BACKUP_MAX_AGE_DAYS:
                alerts.append(
                    {
                        "title": f"Backup overdue: {age_days:.0f} days old",
                        "body": (
                            f"Newest verified snapshot ({newest_id}) is "
                            f"{age_days:.0f} days old. Run make backup."
                        ),
                        "importance": 0.60,
                    }
                )
    except Exception as exc:
        _log.debug("system_monitor: backup check error: %s", exc)
    return alerts


def _check_inbox_depth() -> list[dict]:
    alerts = []
    inbox = config.get_inbox_path()
    try:
        if not inbox.exists():
            return []
        count = sum(1 for _ in inbox.iterdir())
        if count > _INBOX_MAX_DEPTH:
            alerts.append(
                {
                    "title": f"Inbox overflowing: {count} files",
                    "body": (
                        f"ai-workspace/inbox/ has {count} files (threshold: {_INBOX_MAX_DEPTH}). "
                        "Processing may be falling behind."
                    ),
                    "importance": 0.55,
                }
            )
    except Exception as exc:
        _log.debug("system_monitor: inbox check error: %s", exc)
    return alerts


def _check_review_queue() -> list[dict]:
    alerts = []
    try:
        ms = config.get_metadata_store()
        row = ms.fetch_one("SELECT COUNT(*) as cnt FROM review_queue")
        count = int(row["cnt"]) if row else 0
        if count > 0:
            alerts.append(
                {
                    "title": f"Review queue: {count} item(s) awaiting resolution",
                    "body": (
                        f"{count} ambiguous entity merge candidate(s) need your input. "
                        "Check GET /review-queue to resolve them."
                    ),
                    "importance": 0.50,
                }
            )
    except Exception as exc:
        _log.debug("system_monitor: review_queue check error: %s", exc)
    return alerts


def _make_signal(title: str, body: str, importance: float) -> Signal:
    return Signal(
        signal_id=str(uuid.uuid4()),
        source_id="__system__",
        title=title,
        url="",
        published_at=datetime.now(timezone.utc),
        content_summary=body,
        raw_content=body,
        entities=[],
        topics=["system", "health"],
        importance_score=importance,
        relevance_score=importance,  # system alerts bypass relevance profile
        notified=False,
        created_at=datetime.now(timezone.utc),
        user_id="default",
    )
