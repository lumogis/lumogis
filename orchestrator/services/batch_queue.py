# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Per-user durable batch job queue (Postgres + handler registry)."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from datetime import datetime
from datetime import timezone
from typing import Any

from pydantic import BaseModel

import config

_log = logging.getLogger(__name__)

_KIND_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_handlers: dict[str, tuple[Callable[..., None], type[BaseModel]]] = {}

BATCH_QUEUE_PER_USER_MAX_CONCURRENT = int(
    os.environ.get("BATCH_QUEUE_PER_USER_MAX_CONCURRENT", "2")
)
BATCH_QUEUE_MAX_ATTEMPTS = int(os.environ.get("BATCH_QUEUE_MAX_ATTEMPTS", "3"))
BATCH_QUEUE_TICK_SECONDS = int(os.environ.get("BATCH_QUEUE_TICK_SECONDS", "5"))
BATCH_QUEUE_TICK_DRAIN_LIMIT = int(os.environ.get("BATCH_QUEUE_TICK_DRAIN_LIMIT", "8"))
BATCH_QUEUE_STUCK_AFTER_SECONDS = int(os.environ.get("BATCH_QUEUE_STUCK_AFTER_SECONDS", "1800"))
BATCH_QUEUE_STUCK_SWEEPER_SECONDS = int(
    os.environ.get("BATCH_QUEUE_STUCK_SWEEPER_SECONDS", "60")
)

_CLAIM_PREFIX = (
    "WITH next_eligible AS ( "
    "SELECT id FROM user_batch_jobs WHERE status = 'pending' "
    "AND run_after <= NOW() AND ( SELECT COUNT(*) FROM user_batch_jobs r "
)
# SCOPE-EXEMPT: user_batch_jobs is operational queue state, no scope column
_CLAIM_SQL = (
    _CLAIM_PREFIX
    + "WHERE r.user_id = user_batch_jobs.user_id AND r.status = 'running' "
    ") < %s ORDER BY id ASC LIMIT 1 FOR UPDATE SKIP LOCKED ) "
    "UPDATE user_batch_jobs SET status = 'running', started_at = NOW(), worker_id = %s "
    "FROM next_eligible WHERE user_batch_jobs.id = next_eligible.id "
    "RETURNING user_batch_jobs.id, user_batch_jobs.user_id, user_batch_jobs.kind, "
    "user_batch_jobs.payload, user_batch_jobs.attempt, user_batch_jobs.enqueued_at"
)

_ENQUEUE_SQL = (
    "INSERT INTO user_batch_jobs (user_id, kind, payload, run_after) "
    "VALUES (%s, %s, %s::jsonb, %s) RETURNING id"
)

_COMPLETE_SQL = """
UPDATE user_batch_jobs SET
    status = 'done',
    finished_at = NOW()
WHERE id = %s
""".strip()

_FAIL_SQL = """
UPDATE user_batch_jobs SET
    attempt = attempt + 1,
    error = LEFT(%s, 1000),
    status = CASE WHEN attempt + 1 < %s THEN 'pending' ELSE 'dead' END,
    finished_at = CASE WHEN attempt + 1 < %s THEN NULL ELSE NOW() END,
    run_after = CASE
        WHEN attempt + 1 < %s THEN NOW() + (%s * INTERVAL '1 minute')
        ELSE run_after
    END,
    worker_id = NULL,
    started_at = NULL
WHERE id = %s AND status = 'running'
""".strip()

_RESET_STUCK_PENDING_SQL = """
UPDATE user_batch_jobs SET
    status = 'pending',
    worker_id = NULL,
    started_at = NULL,
    attempt = attempt + 1
WHERE status = 'running'
  AND started_at < NOW() - (%s * INTERVAL '1 second')
  AND attempt < %s
RETURNING id
""".strip()

_RESET_STUCK_DEAD_SQL = """
UPDATE user_batch_jobs SET
    status = 'dead',
    finished_at = NOW()
WHERE status = 'running'
  AND started_at < NOW() - (%s * INTERVAL '1 second')
  AND attempt >= %s
RETURNING id
""".strip()


class ClaimedJob(BaseModel):
    id: int
    user_id: str
    kind: str
    payload: dict[str, Any]
    attempt: int
    enqueued_at: datetime


def register_batch_handler(kind: str, payload_model: type[BaseModel]):
    """Decorator: register ``kind`` → handler + Pydantic payload model."""

    if not _KIND_RE.fullmatch(kind):
        raise ValueError(f"invalid batch handler kind: {kind!r}")

    def decorator(fn: Callable[..., None]) -> Callable[..., None]:
        if kind in _handlers:
            raise ValueError(f"duplicate batch handler kind: {kind!r}")
        _handlers[kind] = (fn, payload_model)
        return fn

    return decorator


def enqueue(
    *,
    user_id: str,
    kind: str,
    payload: dict | BaseModel,
    run_after: datetime | None = None,
) -> int:
    if kind not in _handlers:
        raise ValueError(f"unknown batch handler kind: {kind!r}")
    _, payload_model = _handlers[kind]
    raw: dict[str, Any]
    if isinstance(payload, BaseModel):
        raw = payload.model_dump()
    else:
        raw = dict(payload)
    validated = payload_model.model_validate(raw)

    ms = config.get_metadata_store()
    payload_json = json.dumps(validated.model_dump())
    run_after_dt = run_after if run_after is not None else datetime.now(timezone.utc)

    row = ms.fetch_one(
        _ENQUEUE_SQL,
        (user_id, kind, payload_json, run_after_dt),
    )
    if not row or row.get("id") is None:
        raise RuntimeError("enqueue: INSERT returned no id")
    return int(row["id"])


def claim_next(worker_id: str) -> ClaimedJob | None:
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        _CLAIM_SQL,
        (BATCH_QUEUE_PER_USER_MAX_CONCURRENT, worker_id),
    )
    if not row:
        return None
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return ClaimedJob(
        id=int(row["id"]),
        user_id=str(row["user_id"]),
        kind=str(row["kind"]),
        payload=dict(payload) if payload is not None else {},
        attempt=int(row["attempt"]),
        enqueued_at=row["enqueued_at"],
    )


def complete(job_id: int) -> None:
    ms = config.get_metadata_store()
    ms.execute(_COMPLETE_SQL, (job_id,))


def fail(job_id: int, error: str, *, max_attempts: int) -> None:
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT attempt FROM user_batch_jobs WHERE id = %s AND status = 'running'",
        (job_id,),
    )
    if not row:
        return
    new_attempt = int(row["attempt"]) + 1
    backoff_minutes = 2**new_attempt
    ms.execute(
        _FAIL_SQL,
        (error, max_attempts, max_attempts, max_attempts, backoff_minutes, job_id),
    )


def reset_stuck(*, stuck_after_seconds: int, max_attempts: int) -> int:
    ms = config.get_metadata_store()
    n1 = len(
        ms.fetch_all(
            _RESET_STUCK_PENDING_SQL,
            (stuck_after_seconds, max_attempts),
        )
    )
    n2 = len(
        ms.fetch_all(
            _RESET_STUCK_DEAD_SQL,
            (stuck_after_seconds, max_attempts),
        )
    )
    return n1 + n2


def _run_one_tick(worker_id: str) -> bool:
    job = claim_next(worker_id)
    if not job:
        return False
    entry = _handlers.get(job.kind)
    if not entry:
        _log.error(
            "batch_queue: no handler for kind=%s job_id=%s",
            job.kind,
            job.id,
            extra={"user_id": job.user_id, "kind": job.kind, "job_id": job.id, "attempt": job.attempt},
        )
        fail(job.id, f"no handler registered for kind {job.kind!r}", max_attempts=BATCH_QUEUE_MAX_ATTEMPTS)
        return True

    handler, payload_model = entry
    try:
        model = payload_model.model_validate(job.payload)
        _log.info(
            "batch_queue: dispatch kind=%s job_id=%s",
            job.kind,
            job.id,
            extra={"user_id": job.user_id, "kind": job.kind, "job_id": job.id, "attempt": job.attempt},
        )
        handler(user_id=job.user_id, payload=model)
        complete(job.id)
        _log.info(
            "batch_queue: complete kind=%s job_id=%s",
            job.kind,
            job.id,
            extra={"user_id": job.user_id, "kind": job.kind, "job_id": job.id, "attempt": job.attempt},
        )
    except Exception as exc:
        _log.exception(
            "batch_queue: handler failed kind=%s job_id=%s",
            job.kind,
            job.id,
            extra={"user_id": job.user_id, "kind": job.kind, "job_id": job.id, "attempt": job.attempt},
        )
        fail(job.id, repr(exc), max_attempts=BATCH_QUEUE_MAX_ATTEMPTS)
    return True
