# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Claim-and-run bridge for Postgres ``action_proposals`` (LUM-123, LUM-762).

``actions.executor.execute`` stays the sole execution entry point; this module
handles the atomic claim, permission-adjacent pre-flight (**hard-limited** action
types → **403** before claim), bookkeeping, and disposal via
``proposal_queue.dead_letter`` on handler failure.

**Every failure is terminal on the first attempt** (LUM-762). There is no retry
ladder: ``fail_execution`` and its ``executing → approved`` edge are gone, because
nothing drained the queue and a redrive of an unkeyed third-party SMTP submission
would be a duplicate email rather than a retry.
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
            # LUM-762: `_CLAIM_BY_ID_SQL` now filters `run_after <= NOW()`, so an
            # `approved`, unclaimed row deferred to the future fails to claim. Without
            # this branch it falls through to `400 invalid_proposal_state` naming
            # `approved` — a 400 whose detail is the one state that is supposed to be
            # claimable. 409 rather than 400 because the request is well-formed and the
            # state is legal; it is a timing conflict, which is what this route's
            # existing `proposal_claim_conflict` already uses 409 for.
            #
            # Unreachable in production today (nothing writes a future `run_after` once
            # `fail_execution` is gone) but reachable for rows stranded by the PRE-LUM-762
            # retry ladder, and fully live once LUM-147's NOT_NOW writes the field.
            if pk_status == "approved" and peek.get("claimed_at") is None:
                deferred_until = _as_utc(peek.get("run_after"))
                if deferred_until is not None and deferred_until > datetime.now(timezone.utc):
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        detail={
                            "error": "proposal_deferred",
                            "proposal_id": proposal_id,
                            "retry_after": deferred_until.isoformat(),
                        },
                    )
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

        return _finish_claim(claimed, user_id=user_id)
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
) -> ActionResult:
    audit = structlog.get_logger("lumogis.audit")
    pid = claimed.id
    payload: dict[str, Any] = dict(claimed.payload)
    action_name = claimed.action_name
    try:
        # The row reached status='approved' via a human approval, so the
        # per-message-approval gate is satisfied for an ASK-mode write (LUM-623).
        # The stored risk annotation (LUM-650) is threaded through so the Do→Ask
        # first-contact gate reads what the user was shown at approval — never a
        # recomputation. Because this path is `approved=True` (a human approved),
        # a first-contact proposal still executes; the gate's teeth are on the
        # non-approved DO-mode direct path.
        result = execute(
            action_name,
            payload,
            user_id=user_id,
            approved=True,
            annotation=dict(claimed.annotation or {}),
            # LUM-793: this is the ONLY caller that knows the proposal id, so it is
            # the only place the link to `email_sent_log` can originate. The send
            # handlers write it at submission time, which is what lets a recovery
            # surface later tell whether a STRANDED proposal's message went out —
            # a row that never reached the bookkeeping below.
            proposal_id=pid,
        )
    except OperationalError:
        # Best-effort, and it must be: `dead_letter` is itself an UPDATE, so during
        # a Postgres outage it raises too. Swallow only that, so the ORIGINAL
        # OperationalError still propagates (503 is the honest answer) and the row
        # is left `executing` for `reset_stuck_claims` to reconcile when Postgres
        # returns. That is exactly the case the sweeper exists for.
        try:
            proposal_queue.dead_letter(pid, "execute_operational_error")
        except Exception:  # noqa: BLE001 - the outage is the story, not the disposal
            _log.warning("proposal_execute: dead_letter failed during outage pid=%s", pid)
        raise
    except Exception as exc:  # noqa: BLE001
        # Near-unreachable from handler code: `actions.executor.execute` catches
        # everything and returns `success=False` instead (see the `else` below).
        # Kept for anything raised outside the handler. `dead_letter` here can also
        # raise OperationalError mid-outage; that propagates to the caller's outer
        # `except OperationalError` → 503, row left `executing`, sweeper reconciles.
        proposal_queue.dead_letter(pid, repr(exc))
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
        # LUM-762: ONE disposal. `result.permanent` no longer selects between
        # dead-lettering and a retry ladder — there is no ladder. It survives
        # because it still selects the error CODE the composer renders
        # (`SendConfirmCard.tsx`), so do not delete it as dead.
        #
        # `Unknown action: …` arrives here too (executor.py returns it non-permanent
        # before any permission check). Its error string is passed through verbatim
        # and deliberately: it is the one `dead` population that is retryable in
        # spirit, so LUM-793's failed-send listing needs to tell that class apart.
        #
        # It used to have a second, worse cause: `main.py` registered the handlers
        # inside the `BATCH_QUEUE_ENABLED` gate, so an operator disabling the batch
        # queue emptied the action registry and every approved proposal died here.
        # Fixed in LUM-794 — registration is now unconditional. `Unknown action`
        # therefore means a genuinely absent action (removed or renamed across a
        # release), NOT a batch-queue misconfiguration.
        proposal_queue.dead_letter(pid, result.error or "action_failed")

    return result
