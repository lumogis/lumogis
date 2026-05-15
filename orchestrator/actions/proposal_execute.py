# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Claim-and-run bridge for Postgres ``action_proposals`` (LUM-123).

``actions.executor.execute`` stays the sole execution entry point; this module
handles SKIP-LOCKED claim, permission-adjacent pre-flight (**hard-limited**
action types → **403** before claim), bookkeeping, and ``fail_execution`` on
handler failure (:mod:`services.proposal_queue`).
"""

from __future__ import annotations

import logging
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any

import structlog
from actions.executor import execute
from actions.executor import is_hard_limited
from actions.registry import get_action
from fastapi import HTTPException
from fastapi import status
from models.actions import ActionResult
from psycopg2 import OperationalError

from services import proposal_queue

_log = logging.getLogger(__name__)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _within_claim_ttl(claimed_at_raw: datetime | None, ttl_seconds: int) -> bool:
    claimed_at = _as_utc(claimed_at_raw)
    if claimed_at is None:
        return False
    now = datetime.now(timezone.utc)
    return now - claimed_at < timedelta(seconds=ttl_seconds)


def _dispatcher_409(proposal_id: int) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT,
        detail={
            "error": "proposal_claim_conflict",
            "proposal_id": proposal_id,
            "code": "already_claimed",
        },
    )


def claim_and_execute_proposal(proposal_id: int, *, worker_id: str, user_id: str) -> ActionResult:
    """Atomically claim an **approved** row and run ``execute()``; bookkeeping on success."""

    ttl = proposal_queue.ACTION_PROPOSALS_CLAIM_STUCK_AFTER_SECONDS
    max_attempts = proposal_queue.ACTION_PROPOSALS_MAX_ATTEMPTS
    audit = structlog.get_logger("lumogis.audit")

    try:
        row0 = proposal_queue.select_proposal_for_user(proposal_id, user_id)
        if row0 is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail={"error": "not_found"},
            )

        if row0.get("status") == "approved":
            spec = get_action(str(row0.get("action_name") or ""))
            if spec is not None and is_hard_limited(spec.action_type):
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    detail={
                        "error": "hard_limited_proposal",
                        "action_name": spec.name,
                    },
                )

        claimed = proposal_queue.claim_by_id(proposal_id, user_id, worker_id)
        if claimed is None:
            peek = proposal_queue.select_proposal_for_user(proposal_id, user_id)
            if peek is None:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    detail={"error": "not_found"},
                )
            pk_status = peek.get("status")
            if pk_status == "approved" and peek.get("claimed_at") is not None:
                audit.warning(
                    "audit.proposal.lost_claim",
                    proposal_id=proposal_id,
                    user_id=user_id,
                    reason="already_claimed",
                )
                raise _dispatcher_409(proposal_id)
            if pk_status == "executing":
                other = peek.get("claimed_by")
                cat_raw = peek.get("claimed_at")
                if other is not None and other != worker_id and _within_claim_ttl(cat_raw, ttl):
                    audit.warning(
                        "audit.proposal.lost_claim",
                        proposal_id=proposal_id,
                        user_id=user_id,
                        reason="already_claimed",
                    )
                    raise _dispatcher_409(proposal_id)
                if other == worker_id:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        detail={
                            "error": "invalid_proposal_state",
                            "status": pk_status,
                        },
                    )

            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "invalid_proposal_state",
                    "status": pk_status,
                },
            )

        return _finish_claim(
            claimed,
            user_id=user_id,
            max_attempts=max_attempts,
        )
    except HTTPException:
        raise
    except OperationalError as exc:
        _log.warning("proposal_execute: operational error: %s", exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "service_unavailable"},
        ) from exc


def _finish_claim(
    claimed: proposal_queue.ClaimedProposal,
    *,
    user_id: str,
    max_attempts: int,
) -> ActionResult:
    audit = structlog.get_logger("lumogis.audit")
    pid = claimed.id
    payload: dict[str, Any] = dict(claimed.payload)
    action_name = claimed.action_name
    try:
        result = execute(action_name, payload, user_id=user_id)
    except OperationalError:
        proposal_queue.fail_execution(pid, "execute_operational_error", max_attempts=max_attempts)
        raise
    except Exception as exc:  # noqa: BLE001
        proposal_queue.fail_execution(pid, repr(exc), max_attempts=max_attempts)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "internal_error"},
        ) from exc

    if result.success:
        try:
            proposal_queue.mark_done(pid)
        except OperationalError:
            audit.error(
                "proposal_execute.bookkeeping_failed",
                proposal_id=pid,
                user_id=user_id,
            )
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": "bookkeeping_failed", "proposal_id": pid},
            ) from None
        except Exception as exc:  # noqa: BLE001
            _log.exception(
                "proposal_execute: mark_done failed proposal_id=%s user=%s",
                pid,
                user_id,
            )
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": "bookkeeping_failed", "proposal_id": pid},
            ) from exc
    else:
        err = result.error or "action_failed"
        proposal_queue.fail_execution(pid, err, max_attempts=max_attempts)

    return result
