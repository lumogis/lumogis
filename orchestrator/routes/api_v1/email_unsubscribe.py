# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unsubscribe + sender-suppression routes (LUM-697).

Three surfaces:

* ``GET  /api/v1/email/subscriptions`` — the sender-grouped list. Note this is a
  different axis from the cleanup pass, which groups by *category*.
* ``POST /api/v1/email/unsubscribe/propose`` — resolves sender addresses to
  unsubscribe recipients **server-side**, then creates the proposal. The client
  posts senders only; a recipient never crosses the boundary inbound, and the
  approved payload is byte-for-byte the payload that sends.
* ``GET|POST|DELETE /api/v1/email/sender-rules`` — standing ``hide`` rules.

**No outbound HTTP.** ``https_uri`` is returned so the *browser* can follow it;
nothing here fetches it. See ``cursor/adrs/email-unsubscribe.md``.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections import deque
from typing import Deque
from typing import Literal
from typing import Optional

from actions.errors import PermanentActionError
from actions.handlers.smtp_send import MAX_UNSUBSCRIBE_BATCH
from auth import get_user
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi import status
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from services import email_sender_rules
from services import email_unsubscribe

router = APIRouter(prefix="/api/v1/email", tags=["email-unsubscribe"])

# A SEPARATE bucket from the draft/refine/quote limiters: those budget LLM calls,
# these are DB writes. Sharing would let rule-spam starve drafting and vice versa.
_RULES_WINDOW_SEC = 60.0
_RULES_LIMIT = 30
_rules_calls: dict[str, Deque[float]] = defaultdict(deque)

_MAX_LIST_LIMIT = 500
_DEFAULT_LIST_LIMIT = 200


def _rate_check(user_id: str) -> bool:
    now = time.monotonic()
    q = _rules_calls[user_id]
    while q and now - q[0] > _RULES_WINDOW_SEC:
        q.popleft()
    if len(q) >= _RULES_LIMIT:
        return False
    q.append(now)
    return True


def _require_owned(source_id: str, user_id: str) -> None:
    """404 — never 403 — for a mailbox the caller does not own. A 403 confirms
    the source exists, which is the whole family's convention."""
    from actions.handlers.imap_writeback import _load_owned_source

    try:
        _load_owned_source(source_id, user_id)
    except PermanentActionError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")


class SubscriptionRow(BaseModel):
    source_id: str
    from_addr: str
    from_display: str = ""
    message_count: int = 0
    has_mailto: bool = False
    has_https: bool = False
    one_click: bool = False
    # Returned so the BROWSER can follow it. The web layer renders it only when
    # the scheme is https — a second gate, because a row written before a parser
    # fix would otherwise still be live (CVE-2025-68673 is exactly that gap).
    https_uri: str = ""
    last_seen_at: Optional[object] = None
    unsubscribed_at: Optional[object] = None
    suppressed_as: Optional[str] = None


class SubscriptionsResponse(BaseModel):
    rows: list[SubscriptionRow] = []
    truncated: bool = False


class UnsubscribeProposeRequest(BaseModel):
    """What the CLIENT posts: sender addresses only."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    # Bound by the action's own cap rather than a second literal: a route cap
    # below it would silently cap the feature, and one above it would let a
    # proposal be created that the executor then rejects at validation.
    senders: list[str] = Field(min_length=1, max_length=MAX_UNSUBSCRIBE_BATCH)


class ResolvedTargetOut(BaseModel):
    from_addr: str
    mailto_addr: str
    subject: str


class UnsubscribeProposeResponse(BaseModel):
    proposal_id: int
    targets: list[ResolvedTargetOut] = []
    # Senders with no mailto: form — they need the browser hand-off instead.
    skipped: list[str] = []


class UnsubscribeJobTarget(BaseModel):
    from_addr: str
    unsubscribed: bool = False


class UnsubscribeJobProgress(BaseModel):
    """Per-target progress for one batch — not just running/done (LUM-710)."""

    job_id: int
    status: str
    attempt: int = 0
    source_id: str = ""
    enqueued_at: Optional[object] = None
    started_at: Optional[object] = None
    finished_at: Optional[object] = None
    total: int = 0
    sent: int = 0
    outstanding: int = 0
    targets: list[UnsubscribeJobTarget] = []


class UnsubscribeJobSummary(BaseModel):
    job_id: int
    status: str
    total: int = 0
    enqueued_at: Optional[object] = None
    finished_at: Optional[object] = None


class UnsubscribeJobsResponse(BaseModel):
    jobs: list[UnsubscribeJobSummary] = []


class SenderRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    from_addr: str
    action: Literal["hide"] = "hide"


class SenderRuleRow(BaseModel):
    source_id: str
    from_addr: str
    action: str
    active: bool
    created_at: Optional[object] = None


class SenderRulesResponse(BaseModel):
    rules: list[SenderRuleRow] = []


@router.get(
    "/subscriptions", response_model=SubscriptionsResponse, dependencies=[Depends(require_user)]
)
def list_subscriptions(
    request: Request,
    source_id: Optional[str] = Query(default=None),
    limit: int = Query(default=_DEFAULT_LIST_LIMIT, ge=1, le=_MAX_LIST_LIMIT),
) -> SubscriptionsResponse:
    user = get_user(request)
    if source_id:
        _require_owned(source_id, user.user_id)
    rows = email_unsubscribe.list_targets(user.user_id, source_id=source_id, limit=limit + 1)
    truncated = len(rows) > limit
    return SubscriptionsResponse(
        rows=[SubscriptionRow(**r) for r in rows[:limit]], truncated=truncated
    )


@router.post(
    "/unsubscribe/propose",
    response_model=UnsubscribeProposeResponse,
    dependencies=[Depends(require_user)],
)
def propose_unsubscribe(
    request: Request, body: UnsubscribeProposeRequest
) -> UnsubscribeProposeResponse:
    """Resolve senders → recipients server-side, then create ONE proposal.

    Resolving here rather than at execute means the payload the approver sees is
    the payload that sends: a message arriving between approval and execute
    cannot change where the mail goes.
    """
    user = get_user(request)
    if not _rate_check(user.user_id):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="rate_limited")
    _require_owned(body.source_id, user.user_id)

    resolved, skipped = email_unsubscribe.resolve_targets(
        user.user_id, body.source_id, body.senders
    )
    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="no_mailto_targets",
        )

    from services import action_proposals

    proposal_id = action_proposals.create_proposal(
        action_name="email_unsubscribe",
        payload={"source_id": body.source_id, "targets": resolved},
        user_id=user.user_id,
    )
    return UnsubscribeProposeResponse(
        proposal_id=proposal_id,
        targets=[ResolvedTargetOut(**t) for t in resolved],
        skipped=skipped,
    )


@router.get(
    "/unsubscribe/jobs",
    response_model=UnsubscribeJobsResponse,
    dependencies=[Depends(require_user)],
)
def list_unsubscribe_jobs(
    request: Request,
    limit: int = Query(default=10, ge=1, le=50),
) -> UnsubscribeJobsResponse:
    """This user's recent unsubscribe batches, newest first.

    How a client finds the job it just approved: the executor's result is a
    human-readable string, so there is no id to carry forward from the approval.
    """
    user = get_user(request)
    return UnsubscribeJobsResponse(
        jobs=[
            UnsubscribeJobSummary(**j)
            for j in email_unsubscribe.list_batch_jobs(user.user_id, limit=limit)
        ]
    )


@router.get(
    "/unsubscribe/jobs/{job_id}",
    response_model=UnsubscribeJobProgress,
    dependencies=[Depends(require_user)],
)
def get_unsubscribe_job(request: Request, job_id: int) -> UnsubscribeJobProgress:
    """Per-target progress for one batch. 404 for anyone else's job.

    404 rather than 403 — the family convention — and here it is also the only
    safe answer: a 403 would confirm the job id exists, which is enough to walk
    the sequence and count another member's unsubscribe activity.
    """
    user = get_user(request)
    row = email_unsubscribe.batch_job_progress(user.user_id, job_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return UnsubscribeJobProgress.model_validate(row)


@router.get(
    "/sender-rules", response_model=SenderRulesResponse, dependencies=[Depends(require_user)]
)
def list_sender_rules(
    request: Request, source_id: Optional[str] = Query(default=None)
) -> SenderRulesResponse:
    user = get_user(request)
    if source_id:
        _require_owned(source_id, user.user_id)
    return SenderRulesResponse(
        rules=[
            SenderRuleRow(**r)
            for r in email_sender_rules.list_rules(user.user_id, source_id=source_id)
        ]
    )


@router.post("/sender-rules", dependencies=[Depends(require_user)])
def set_sender_rule(request: Request, body: SenderRuleRequest) -> dict:
    user = get_user(request)
    if not _rate_check(user.user_id):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="rate_limited")
    _require_owned(body.source_id, user.user_id)
    return email_sender_rules.set_rule(user.user_id, body.source_id, body.from_addr, body.action)


@router.delete("/sender-rules", dependencies=[Depends(require_user)])
def clear_sender_rule(
    request: Request,
    source_id: str = Query(...),
    from_addr: str = Query(...),
) -> dict:
    user = get_user(request)
    _require_owned(source_id, user.user_id)
    removed = email_sender_rules.clear_rule(user.user_id, source_id, from_addr)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    # Go-forward only: clearing a rule cannot resurface mail already hidden (no
    # signals row, poll cursor advanced). LUM-711's backfill is the only path.
    return {"status": "cleared"}
