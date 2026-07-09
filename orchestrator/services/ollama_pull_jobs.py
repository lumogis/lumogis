# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Postgres-backed async Ollama pull jobs (LUM-449)."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any

import ollama_client

import config

_log = logging.getLogger(__name__)

_EMBED_COLLECTIONS = ("documents", "conversations", "entities", "signals")

QDRANT_INIT_WARNING_MSG = (
    "Qdrant collections could not be initialized after pull. Restart the orchestrator to retry."
)


class JobAlreadyRunning(Exception):
    """Raised when a pending or running pull job already exists."""


def _retention_hours() -> int:
    raw = os.environ.get("OLLAMA_PULL_JOB_RETENTION_HOURS", "24").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 24


def _meta():
    return config.get_metadata_store()


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def job_to_response(row: dict[str, Any]) -> dict[str, Any]:
    """Serialize a DB row to the stable poll JSON contract."""
    return {
        "job_id": str(row["job_id"]),
        "model_name": row["model_name"],
        "status": row["status"],
        "progress_pct": row.get("progress_pct"),
        "status_message": row.get("status_message"),
        "error_message": row.get("error_message"),
        "qdrant_init_warning": row.get("qdrant_init_warning"),
        "created_at": _iso(row.get("created_at")),
        "started_at": _iso(row.get("started_at")),
        "finished_at": _iso(row.get("finished_at")),
    }


def assert_no_running_job() -> None:
    row = _meta().fetch_one(
        "SELECT job_id FROM ollama_pull_jobs WHERE status IN ('pending', 'running') LIMIT 1"
    )
    if row is not None:
        raise JobAlreadyRunning()


def _purge_terminal_rows() -> None:
    hours = _retention_hours()
    _meta().execute(
        "DELETE FROM ollama_pull_jobs "
        "WHERE status IN ('succeeded', 'failed') "
        "AND finished_at IS NOT NULL "
        "AND finished_at < NOW() - (%s || ' hours')::interval",
        (str(hours),),
    )


def _mark_stale_running_failed() -> None:
    _meta().execute(
        "UPDATE ollama_pull_jobs SET status = 'failed', "
        "error_message = 'stale (orchestrator restart?)', finished_at = NOW() "
        "WHERE status = 'running' "
        "AND started_at IS NOT NULL "
        "AND started_at < NOW() - interval '2 hours'"
    )


def create_job(model_name: str) -> str:
    """Insert a pending job and return its job_id string."""
    _purge_terminal_rows()
    _mark_stale_running_failed()
    assert_no_running_job()
    row = _meta().fetch_one(
        "INSERT INTO ollama_pull_jobs (model_name) VALUES (%s) RETURNING job_id",
        (model_name,),
    )
    return str(row["job_id"])


def get_job(job_id: str) -> dict[str, Any] | None:
    return _meta().fetch_one(
        "SELECT * FROM ollama_pull_jobs WHERE job_id = %s",
        (job_id,),
    )


def get_active_job() -> dict[str, Any] | None:
    return _meta().fetch_one(
        "SELECT * FROM ollama_pull_jobs "
        "WHERE status IN ('pending', 'running') "
        "ORDER BY created_at DESC LIMIT 1"
    )


def finalize_ollama_pull(name: str, app_state: Any) -> str | None:
    """LibreChat sync + Qdrant init after a successful pull."""
    from routes.admin import _sync_librechat_config

    _sync_librechat_config()

    if name.split(":")[0] != os.environ.get("EMBEDDING_MODEL", "nomic-embed-text").split(":")[0]:
        return None

    from services.embedding_readiness import try_activate_embedding

    if not try_activate_embedding(app_state):
        _log.warning(
            "Could not initialize Qdrant collections after pull. "
            "Restart the orchestrator or wait for the readiness retry job.",
        )
        return QDRANT_INIT_WARNING_MSG
    return None


def _set_running(job_id: str) -> None:
    _meta().execute(
        "UPDATE ollama_pull_jobs SET status = 'running', started_at = NOW() WHERE job_id = %s",
        (job_id,),
    )


def _update_progress(
    job_id: str,
    progress_pct: int | None,
    status_message: str | None,
) -> None:
    msg = (status_message or "")[:500] or None
    _meta().execute(
        "UPDATE ollama_pull_jobs SET progress_pct = %s, status_message = %s WHERE job_id = %s",
        (progress_pct, msg, job_id),
    )


def _finish_job(
    job_id: str,
    *,
    status: str,
    error_message: str | None = None,
    qdrant_init_warning: str | None = None,
) -> None:
    err = (error_message or "")[:1000] or None
    _meta().execute(
        "UPDATE ollama_pull_jobs SET status = %s, error_message = %s, "
        "qdrant_init_warning = %s, finished_at = NOW() "
        "WHERE job_id = %s",
        (status, err, qdrant_init_warning, job_id),
    )


def run_pull_job(job_id: str, app_state: Any) -> None:
    """Background worker: stream pull progress and finalize on success."""
    row = get_job(job_id)
    if row is None:
        _log.warning("ollama pull job %s missing at worker start", job_id)
        return
    model_name = row["model_name"]
    _set_running(job_id)
    last_write = 0.0
    last_pct: int | None = None
    try:
        for event in ollama_client.iter_pull_progress(model_name):
            now = time.monotonic()
            pct = event.get("progress_pct")
            should_write = (
                now - last_write >= 1.0
                or (pct is not None and last_pct is not None and abs(pct - last_pct) >= 1)
                or (pct is not None and last_pct is None)
            )
            if should_write:
                _update_progress(job_id, pct, event.get("status"))
                last_write = now
                last_pct = pct
        warning = finalize_ollama_pull(model_name, app_state)
        _finish_job(job_id, status="succeeded", qdrant_init_warning=warning)
    except Exception as exc:
        _log.warning("ollama pull job %s failed: %s", job_id, exc)
        _finish_job(job_id, status="failed", error_message=str(exc))
