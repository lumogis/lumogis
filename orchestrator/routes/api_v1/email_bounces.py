# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Bounces the user has not been told about (LUM-820).

``GET /api/v1/email/bounces`` and
``POST /api/v1/email/bounces/{bounce_id}/acknowledge``.

This is the half that closes the ticket. Recording a bounce and keeping it out
of the triage lanes stops Lumogis *losing* the evidence; it does not yet stop
the user believing a message arrived when it did not. Only a surface does that.

**Permanent bounces only** — see ``services.email_dsn.list_unacknowledged``. A
4.x.x is still being retried, and warning about it is how a warning becomes
wallpaper.

The identity comes from the session. There is no ``user_id`` parameter and there
must never be one: this returns which of a person's messages failed to arrive
and to whom, which is a read of their correspondence.
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

from services import email_dsn

router = APIRouter(prefix="/api/v1/email", tags=["email-bounces"])


class BounceOut(BaseModel):
    """One undelivered message.

    ``subject`` is None when the send row has been pruned — the bounce is still
    worth showing, named by recipient alone.
    """

    id: str
    recipient: str
    status: str
    subject: str | None = None
    seen_at: str | None = None
    sent_at: str | None = None


class BouncesResponse(BaseModel):
    bounces: list[BounceOut]


@router.get("/bounces", response_model=BouncesResponse)
def list_bounces(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    _: object = Depends(require_user),
) -> BouncesResponse:
    user = get_user(request)
    rows = email_dsn.list_unacknowledged(user.user_id, limit=limit)
    return BouncesResponse(
        bounces=[
            BounceOut(
                id=str(r.get("id")),
                recipient=str(r.get("recipient") or ""),
                status=str(r.get("status") or ""),
                subject=(str(r["subject"]) if r.get("subject") else None),
                seen_at=(r["seen_at"].isoformat() if r.get("seen_at") else None),
                sent_at=(r["sent_at"].isoformat() if r.get("sent_at") else None),
            )
            for r in rows
        ]
    )


@router.post("/bounces/{bounce_id}/acknowledge", status_code=status.HTTP_204_NO_CONTENT)
def acknowledge_bounce(
    request: Request,
    bounce_id: str,
    _: object = Depends(require_user),
) -> None:
    """Dismiss one notice. 404 for a bounce the caller does not own, never 403."""
    user = get_user(request)
    if not email_dsn.acknowledge(bounce_id, user.user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
