# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Postgres-backed ingest job progress + global SSE fan-out (LUM-511)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any
from typing import Literal

import config
from services.documents import _sanitize_error_message

_log = logging.getLogger(__name__)

IngestProgressStage = Literal[
    "queued",
    "extracting",
    "chunking",
    "embedding",
    "graph",
    # LUM-157 — shared-document content projection (share_document job):
    "projecting",
    "partial",
    "done",
    "failed",
]

STAGE_PROGRESS_PCT: dict[str, int] = {
    "queued": 0,
    "extracting": 15,
    "chunking": 35,
    "embedding": 60,
    "graph": 85,
    # LUM-157: 'projecting' is the mid-flight share stage; 'partial' is a
    # terminal-but-incomplete share outcome (some content still indexing).
    "projecting": 50,
    "partial": 100,
    "done": 100,
    "failed": 100,
}

_BATCH_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# LUM-157: the share/unshare jobs reuse the ingest progress + failure plumbing
# (poll endpoint via get_ingest_job_row, retry/dead handling via
# maybe_handle_ingest_job_failure). They are deliberately NOT added to the
# _run_one_tick terminal-'done' set, so a handler-set 'partial' is preserved.
_INGEST_JOB_KINDS = frozenset(
    {"ingest_upload", "ingest_watch_file", "share_document", "unshare_document"}
)

_UPDATE_PROGRESS_SQL = """
UPDATE user_batch_jobs
SET progress_stage = %s,
    progress_pct = %s,
    progress_message = %s
WHERE id = %s AND user_id = %s
RETURNING id, user_id, kind, payload, status, attempt, progress_stage, progress_pct,
          progress_message, error, enqueued_at, started_at, finished_at
""".strip()


def validate_batch_id(batch_id: str) -> str:
    """Reject unsafe batch id strings for header/path use."""
    raw = batch_id.strip()
    if not raw or not _BATCH_ID_RE.fullmatch(raw):
        raise ValueError("invalid batch_id")
    return raw


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _payload_dict(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _derive_stage(row: dict[str, Any]) -> IngestProgressStage:
    raw = row.get("progress_stage")
    if isinstance(raw, str) and raw in STAGE_PROGRESS_PCT:
        return raw  # type: ignore[return-value]
    status = str(row.get("status") or "")
    if status == "dead":
        return "failed"
    if status == "done":
        return "done"
    if status == "running":
        return "queued"
    if status == "pending":
        return "queued"
    return "queued"


def job_row_to_progress_response(row: dict[str, Any]) -> dict[str, Any]:
    """Stable JSON for poll endpoints and SSE."""
    payload = _payload_dict(row)
    stage = _derive_stage(row)
    status = str(row.get("status") or "pending")
    err: str | None = None
    if status == "dead":
        err = _sanitize_error_message(row.get("error")) or "Ingest failed"
    pct = row.get("progress_pct")
    if pct is None:
        pct = STAGE_PROGRESS_PCT.get(stage)
    return {
        "job_id": int(row["id"]),
        "file_id": payload.get("file_id"),
        "batch_id": payload.get("batch_id"),
        "status": status,
        "stage": stage,
        "progress_pct": int(pct) if pct is not None else None,
        "status_message": row.get("progress_message"),
        "error": err,
        "enqueued_at": _iso(row.get("enqueued_at")),
        "started_at": _iso(row.get("started_at")),
        "finished_at": _iso(row.get("finished_at")),
    }


def _emit_progress_sse(body: dict[str, Any], *, user_id: str) -> None:
    from routes import events as events_routes

    events_routes.enqueue_user_sse("ingest_progress", body, user_id=user_id)


def update_ingest_job_progress(
    *,
    job_id: int,
    user_id: str,
    stage: IngestProgressStage,
    progress_pct: int | None = None,
    status_message: str | None = None,
    emit_sse: bool = True,
) -> dict[str, Any]:
    pct = progress_pct if progress_pct is not None else STAGE_PROGRESS_PCT[stage]
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        _UPDATE_PROGRESS_SQL,
        (stage, pct, status_message, job_id, user_id),
    )
    if not row:
        raise LookupError(f"ingest job not found: job_id={job_id}")
    body = job_row_to_progress_response(row)
    if emit_sse:
        _emit_progress_sse(body, user_id=user_id)
    return body


def get_ingest_job_row(*, job_id: int, user_id: str) -> dict[str, Any] | None:
    ms = config.get_metadata_store()
    return ms.fetch_one(
        """
        SELECT id, user_id, kind, payload, status, attempt, progress_stage, progress_pct,
               progress_message, error, enqueued_at, started_at, finished_at
        FROM user_batch_jobs
        WHERE id = %s AND user_id = %s
          AND kind IN ('ingest_upload', 'ingest_watch_file',
                       'share_document', 'unshare_document')
        """,
        (job_id, user_id),
    )


def get_ingest_batch_summary(*, batch_id: str, user_id: str) -> dict[str, Any]:
    bid = validate_batch_id(batch_id)
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        """
        SELECT
          COUNT(*) FILTER (WHERE status = 'done')::int AS completed,
          COUNT(*) FILTER (WHERE status = 'dead')::int AS failed,
          COUNT(*) FILTER (WHERE status IN ('pending', 'running'))::int AS in_progress
        FROM user_batch_jobs
        WHERE user_id = %s
          AND kind IN ('ingest_upload', 'ingest_watch_file')
          AND payload->>'batch_id' = %s
        """,
        (user_id, bid),
    )
    if not row:
        return {"batch_id": bid, "completed": 0, "failed": 0, "in_progress": 0}
    return {
        "batch_id": bid,
        "completed": int(row.get("completed") or 0),
        "failed": int(row.get("failed") or 0),
        "in_progress": int(row.get("in_progress") or 0),
    }


def mark_ingest_job_done(*, job_id: int, user_id: str) -> None:
    update_ingest_job_progress(
        job_id=job_id,
        user_id=user_id,
        stage="done",
        progress_pct=100,
    )


def mark_ingest_job_failed(*, job_id: int, user_id: str, error: str) -> None:
    msg = _sanitize_error_message(error) or "Ingest failed"
    update_ingest_job_progress(
        job_id=job_id,
        user_id=user_id,
        stage="failed",
        progress_pct=100,
        status_message=msg,
    )


def reset_ingest_job_progress_for_retry(*, job_id: int, user_id: str) -> None:
    update_ingest_job_progress(
        job_id=job_id,
        user_id=user_id,
        stage="queued",
        progress_pct=STAGE_PROGRESS_PCT["queued"],
        status_message=None,
        emit_sse=False,
    )


def maybe_handle_ingest_job_failure(
    *,
    job_id: int,
    user_id: str,
    kind: str,
    new_attempt: int,
    max_attempts: int,
    error: str,
) -> None:
    """Called from ``batch_queue.fail`` after SQL — terminal vs retry progress."""
    if kind not in _INGEST_JOB_KINDS:
        return
    if new_attempt < max_attempts:
        reset_ingest_job_progress_for_retry(job_id=job_id, user_id=user_id)
    else:
        mark_ingest_job_failed(job_id=job_id, user_id=user_id, error=error)
