# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Per-mailbox outgoing signature (LUM-817).

``GET`` / ``PUT /api/v1/email/sources/{source_id}/signature``.

The mailbox is addressed by id and the identity comes from the session, so both
verbs carry the same ownership predicate in SQL rather than a separate check
that could drift from it. A mailbox the caller does not own is **404, never
403** — the same choice `email_search` makes, and for the same reason: 403
confirms the mailbox exists and turns the gate into an oracle over other
people's mailboxes.

A non-owned ``PUT`` therefore updates zero rows and answers 404. It does not
report how many rows it touched, because that count is the oracle in another
shape.
"""

from __future__ import annotations

from auth import get_user
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from pydantic import BaseModel
from pydantic import Field

from services import email_signature

router = APIRouter(prefix="/api/v1/email/sources", tags=["email-signature"])


class SignatureOut(BaseModel):
    signature: str


class SignatureIn(BaseModel):
    signature: str = Field(default="", description="Plain text; empty clears it")


def _require_owned(source_id: str, user_id: str) -> None:
    """404 for a mailbox the caller does not own — never 403."""
    import config

    ms = config.get_metadata_store()
    row = ms.fetch_one(
        # SCOPE-EXEMPT: ownership check on the caller's own mailbox row.
        "SELECT 1 AS ok FROM sources WHERE id::text = %s AND user_id = %s",
        (str(source_id), user_id),
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")


@router.get("/{source_id}/signature", response_model=SignatureOut)
def read_signature(
    request: Request,
    source_id: str,
    _: object = Depends(require_user),
) -> SignatureOut:
    user = get_user(request)
    _require_owned(source_id, user.user_id)
    return SignatureOut(signature=email_signature.get_signature(source_id, user.user_id))


@router.put("/{source_id}/signature", response_model=SignatureOut)
def write_signature(
    request: Request,
    source_id: str,
    body: SignatureIn,
    _: object = Depends(require_user),
) -> SignatureOut:
    user = get_user(request)
    _require_owned(source_id, user.user_id)
    try:
        stored = email_signature.set_signature(source_id, user.user_id, body.signature)
    except email_signature.SignatureTooLong as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
    return SignatureOut(signature=stored)
