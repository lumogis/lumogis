# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Recipient suggestions for the composer (LUM-818).

``GET /api/v1/email/recipients`` — addresses the calling user already
corresponds with, matched as a substring of the address or the display name.

**The identity comes from the session and never from a parameter.** There is no
``user_id`` query argument and there must never be one: this endpoint returns a
list of a person's correspondents, so a caller-supplied identity would turn it
into a read of someone else's address book.

Its own rate bucket, for a reason beyond load. A substring endpoint over
correspondents is an enumeration surface — the data is the caller's own, so this
is not cross-user leakage, but a compromised session could otherwise walk the
whole list in a few hundred requests. The two-character floor in
``services.email_recipients`` bounds the same risk from the other side.

**A suggestion is a memory aid, never a validator.** Choosing one does not mean
the address is deliverable and does not skip any send gate: ``_is_first_contact``
still fires, so writing to a suggested-but-never-before-contacted address still
takes the confirm path (LUM-650 / LUM-652).
"""

from __future__ import annotations

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

from services import email_recipients

router = APIRouter(prefix="/api/v1/email", tags=["email-recipients"])

# Its own bucket: a Postgres read, and an enumeration surface. Sized above the
# search bucket because autocomplete fires per keystroke-burst, not per query.
# 90/min pairs with the client 250ms debounce: enough headroom that a fast
# typist working across To/Cc/Bcc does not hit 429 mid-compose (the client
# swallows failures, so that reads as suggestions silently stopping), while
# still bounding how fast a stolen session could walk the address book. The
# two-character floor is the other half of that bound.
_limiter = RequestRateLimiter(max_requests=90, window_seconds=60)


class RecipientSuggestionOut(BaseModel):
    """One suggestion.

    ``address`` is the **bare addr-spec**, and the composer must insert exactly
    this. The UI may render ``display_name`` alongside it, but the backend's
    ``_validate_recipient`` rejects whitespace, so inserting a
    ``Jane Smith <j@x.com>`` form would surface as a 422 at proposal-create —
    long after the user typed it.
    """

    address: str
    display_name: str
    corresponded: bool


class RecipientSuggestionsResponse(BaseModel):
    suggestions: list[RecipientSuggestionOut]


@router.get("/recipients", response_model=RecipientSuggestionsResponse)
def suggest_recipients(
    request: Request,
    q: str = Query(..., description="Substring of an address or display name"),
    limit: int = Query(
        email_recipients.DEFAULT_LIMIT,
        ge=1,
        le=email_recipients.MAX_LIMIT,
    ),
    _: object = Depends(require_user),
) -> RecipientSuggestionsResponse:
    user = get_user(request)

    if not _limiter.check(user.user_id):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="rate_limited")

    try:
        found = email_recipients.suggest_recipients(user.user_id, q, limit=limit)
    except ValueError as exc:
        # 400 rather than an empty list. An empty list would read as "you have
        # no such correspondent", which for a one-character query is a lie.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

    return RecipientSuggestionsResponse(
        suggestions=[
            RecipientSuggestionOut(
                address=s.address,
                display_name=s.display_name,
                corresponded=s.corresponded,
            )
            for s in found
        ]
    )
