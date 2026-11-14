# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Local search over ingested mail (LUM-819).

``GET /api/v1/email/search`` — one surface, over subject, sender and message
text. The identity is taken from the session and never from a parameter.

The response carries **per-field match counts** alongside the hits. That is not
decoration: subject/sender substring-match while message text word-matches, and
a user cannot see which engine answered. Search fails silently by nature — an
empty list is indistinguishable from an absent message — so the counts are what
make the remaining difference legible rather than mysterious.

Message-text matches are **owner-only** (see ``services/email_search.py``), so a
household member's shared mail will report ``body: 0`` permanently. That is the
same decision LUM-810 made for the read view, surfaced rather than hidden.
"""

from __future__ import annotations

from typing import Literal
from typing import Optional

from actions.errors import PermanentActionError
from auth import get_user
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi import status
from pydantic import BaseModel
from rate_limit import RequestRateLimiter

from services import email_search

router = APIRouter(prefix="/api/v1/email", tags=["email-search"])

# Its own bucket: these are Postgres reads and must not share the LLM budget.
_limiter = RequestRateLimiter(max_requests=30, window_seconds=60)


def _require_owned(source_id: str, user_id: str) -> None:
    """404 — never 403 — for a mailbox the caller does not own.

    A 403 would confirm the source exists, turning the gate into an oracle over
    other users' mailboxes. The whole email family answers 404 here.
    """
    from actions.handlers.imap_writeback import _load_owned_source

    try:
        _load_owned_source(source_id, user_id)
    except PermanentActionError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")


class FieldCounts(BaseModel):
    all: int = 0
    subject: int = 0
    sender: int = 0
    body: int = 0
    #: A count hit the bound; the surface renders "200+". Exact counts cannot
    #: run under the result LIMIT, so they are capped rather than sold as free.
    capped: bool = False


class EmailSearchResponse(BaseModel):
    #: Same row shape ``GET /signals`` returns, plus ``matched_in``,
    #: ``snippet`` and ``body_truncated``. Deliberately not a narrower model:
    #: the web layer's ``collapseProjections`` groups on ``published_from`` and
    #: tie-breaks on ``triage_status``, and ``EmailSignalCard`` reads several
    #: more — a bespoke subset would silently break both.
    hits: list[dict] = []
    counts: FieldCounts = FieldCounts()
    fields: Literal["all", "subject", "sender", "body"] = "all"
    truncated: bool = False
    searched_from: Optional[object] = None


@router.get("/search", response_model=EmailSearchResponse)
def search_email(
    request: Request,
    q: str = Query(..., description="What to look for"),
    fields: Literal["all", "subject", "sender", "body"] = Query("all"),
    source_id: Optional[str] = Query(None, description="Omit to search every mailbox"),
    limit: int = Query(50, ge=1, le=200),
    _: object = Depends(require_user),
) -> EmailSearchResponse:
    user = get_user(request)

    if not _limiter.check(user.user_id):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="rate_limited")

    if source_id:
        _require_owned(source_id, user.user_id)

    try:
        result = email_search.search_email(user, q, fields=fields, source_id=source_id, limit=limit)
    except email_search.QueryTooShort as exc:
        # 400 rather than an empty list: a punctuation-only query matches
        # nothing without raising, so returning [] would be a silent failure —
        # the exact thing this feature exists to remove.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

    return EmailSearchResponse(**result)
