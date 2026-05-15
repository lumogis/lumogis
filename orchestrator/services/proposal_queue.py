# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Postgres-backed action proposal queue — atomic SKIP LOCKED claim (LUM-123).

Mirrors structural patterns from :mod:`services.batch_queue`::

  * Single-statement ``FOR UPDATE SKIP LOCKED`` claim for ``claim_next``
    (locks only for statement duration — see ``PostgresStore`` autocommit).
  * ``fail_execution`` / backoff shape aligned with ``batch_queue._FAIL_SQL``
    (lines 62--74): ``attempt`` bump, exponential minute backoff via
    ``run_after``, ``dead`` past ``max_attempts``.
  * Stale stuck handling is **not** batch-style reset to queued: stale
    ``executing`` / ``claimed`` rows go **dead** with error
    ``stale_executing_claim`` (no ``approved`` re-open — avoid double connector
    side effects).
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import structlog
from pydantic import BaseModel

import config

ACTION_PROPOSALS_CLAIM_STUCK_AFTER_SECONDS = int(
    os.environ.get("ACTION_PROPOSALS_CLAIM_STUCK_AFTER_SECONDS", "300")
)
ACTION_PROPOSALS_CLAIM_SWEEPER_SECONDS = int(
    os.environ.get("ACTION_PROPOSALS_CLAIM_SWEEPER_SECONDS", "60")
)
ACTION_PROPOSALS_MAX_ATTEMPTS = int(os.environ.get("ACTION_PROPOSALS_MAX_ATTEMPTS", "3"))

STALE_EXECUTING_ERROR = "stale_executing_claim"

_CLAIM_NEXT_SQL = """
WITH next_eligible AS (
    SELECT id FROM action_proposals
    WHERE status = 'approved'
      AND claimed_at IS NULL
      AND run_after <= NOW()
    ORDER BY id ASC
    LIMIT 1
    FOR UPDATE SKIP LOCKED
)
UPDATE action_proposals SET
    status = 'executing',
    claimed_at = NOW(),
    claimed_by = %s
FROM next_eligible
WHERE action_proposals.id = next_eligible.id
RETURNING action_proposals.id, action_proposals.user_id, action_proposals.action_name,
          action_proposals.payload, action_proposals.attempt, action_proposals.created_at,
          action_proposals.status
""".strip()

_CLAIM_BY_ID_SQL = """
UPDATE action_proposals SET
    status = 'executing',
    claimed_at = NOW(),
    claimed_by = %s
WHERE id = %s AND user_id = %s AND status = 'approved' AND claimed_at IS NULL
RETURNING id, user_id, action_name, payload, attempt, created_at, status
""".strip()

_MARK_DONE_SQL = """
UPDATE action_proposals SET
    status = 'done',
    executed_at = NOW(),
    finished_at = NOW()
WHERE id = %s AND status = 'executing'
""".strip()

_FAIL_SQL = """
UPDATE action_proposals SET
    attempt = attempt + 1,
    error = LEFT(%s, 1000),
    status = CASE WHEN attempt + 1 < %s THEN 'approved' ELSE 'dead' END,
    finished_at = CASE WHEN attempt + 1 < %s THEN NULL ELSE NOW() END,
    run_after = CASE
        WHEN attempt + 1 < %s THEN NOW() + (%s * INTERVAL '1 minute')
        ELSE run_after
    END,
    claimed_by = NULL,
    claimed_at = NULL,
    executed_at = NULL
WHERE id = %s AND status = 'executing'
""".strip()

_RESET_STUCK_DEAD_SQL = """
UPDATE action_proposals SET
    status = 'dead',
    finished_at = NOW(),
    error = %s,
    claimed_by = NULL,
    claimed_at = NULL
WHERE status IN ('executing', 'claimed')
  AND claimed_at IS NOT NULL
  AND claimed_at < NOW() - (%s * INTERVAL '1 second')
RETURNING id, user_id
""".strip()


class ClaimedProposal(BaseModel):
    id: int
    user_id: str
    action_name: str
    payload: dict[str, Any]
    attempt: int
    created_at: datetime
    status: str


def claim_next(worker_id: str) -> ClaimedProposal | None:
    ms = config.get_metadata_store()
    row = ms.fetch_one(_CLAIM_NEXT_SQL, (worker_id,))
    if not row:
        return None
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return ClaimedProposal(
        id=int(row["id"]),
        user_id=str(row["user_id"]),
        action_name=str(row["action_name"]),
        payload=dict(payload) if payload is not None else {},
        attempt=int(row["attempt"]),
        created_at=row["created_at"],
        status=str(row["status"]),
    )


def claim_by_id(proposal_id: int, user_id: str, worker_id: str) -> ClaimedProposal | None:
    ms = config.get_metadata_store()
    row = ms.fetch_one(_CLAIM_BY_ID_SQL, (worker_id, proposal_id, user_id))
    if not row:
        return None
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return ClaimedProposal(
        id=int(row["id"]),
        user_id=str(row["user_id"]),
        action_name=str(row["action_name"]),
        payload=dict(payload) if payload is not None else {},
        attempt=int(row["attempt"]),
        created_at=row["created_at"],
        status=str(row["status"]),
    )


def mark_done(proposal_id: int) -> None:
    ms = config.get_metadata_store()
    ms.execute(_MARK_DONE_SQL, (proposal_id,))


def fail_execution(proposal_id: int, error: str, *, max_attempts: int) -> None:
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT attempt FROM action_proposals WHERE id = %s AND status = 'executing'",
        (proposal_id,),
    )
    if not row:
        return
    new_attempt = int(row["attempt"]) + 1
    backoff_minutes = 2**new_attempt
    ms.execute(
        _FAIL_SQL,
        (error, max_attempts, max_attempts, max_attempts, backoff_minutes, proposal_id),
    )


def reset_stuck_claims(*, stuck_after_seconds: int) -> int:
    """Dead-letter stale ``executing`` / ``claimed`` rows; emit audit mirrors."""

    ms = config.get_metadata_store()
    rows = ms.fetch_all(
        _RESET_STUCK_DEAD_SQL,
        (STALE_EXECUTING_ERROR, stuck_after_seconds),
    )
    audit = structlog.get_logger("lumogis.audit")
    for row in rows or []:
        audit.warning(
            "audit.proposal.stale_executing_swept",
            proposal_id=int(row["id"]),
            user_id=str(row["user_id"]),
        )
    return len(rows or [])


def select_proposal_for_user(proposal_id: int, user_id: str) -> dict | None:
    """Return lifecycle columns for dispatcher (404 / 400 / 409 / pre-flight)."""
    ms = config.get_metadata_store()
    return ms.fetch_one(
        "SELECT id, status, action_name, payload, claimed_at, claimed_by, run_after "
        "FROM action_proposals WHERE id = %s AND user_id = %s",
        (proposal_id, user_id),
    )


# Exposed for Postgres concurrency tests (`test_proposal_queue_migration`).
CLAIM_NEXT_SQL = _CLAIM_NEXT_SQL
CLAIM_BY_ID_SQL = _CLAIM_BY_ID_SQL
RESET_STUCK_DEAD_SQL = _RESET_STUCK_DEAD_SQL
