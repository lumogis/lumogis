# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The address each of the caller's mailboxes sends FROM (LUM-831).

``GET /api/v1/email/sending-addresses`` — one entry per mailbox the caller can
send from, resolved on demand from the credential rather than stored a second
time.

**The identity comes from the session and never from a parameter.** There is no
``user_id`` argument and there must never be one: these are a person's own
account identifiers.

**Why an endpoint and not a column.** ADR-199 (LUM-634) rejected a plaintext
column on ``user_connector_credentials``; a ``sources.send_address`` would not be
that same column, but it would start a *second copy* of a value with no canonical
plaintext home, reopening the divergence class ADR-197 / LUM-773 closed for
hosts. Reading it on demand means the address shown **cannot drift** from the
address the send uses — both read one field of one row — which is the property
that matters for a control whose failure mode is "I sent that from the wrong
account".

**A mailbox that does not resolve is omitted, not dropped from the UI.** The
client keeps showing it by name. Removing it would take away a mailbox the user
can genuinely send from, which is the LUM-719 mistake in a new place.

``items`` is keyed on credential rows and the web keys on ``sources`` rows, so an
entry the client cannot match is ignored. It is **not** authoritative for "your
mailboxes". Order is unspecified — key by ``source_id``, never by position.
"""

from __future__ import annotations

from auth import get_user
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi import status
from pydantic import BaseModel
from rate_limit import RequestRateLimiter

from services import email_credentials

router = APIRouter(prefix="/api/v1/email", tags=["email-sending-addresses"])

# Its own bucket. Fired once per `/email` mount, not per keystroke, so it is
# sized like the body/quote/draft/refine siblings (20/60s) rather than like
# `email_recipients` (90/60s, which pairs with a 250ms typing debounce).
# Built on the shared limiter, not a local deque: the deques in `email_send.py`
# carry no lock and sync FastAPI handlers run in a threadpool.
_limiter = RequestRateLimiter(max_requests=20, window_seconds=60)


class SendingAddressOut(BaseModel):
    """One mailbox's sending address.

    ``address`` is always a real, non-empty address. A mailbox whose address
    could not be resolved is **absent** from ``items`` rather than present with
    an empty or null address — absence says "no address for this mailbox" once,
    instead of making every consumer distinguish null from not-sendable.
    """

    source_id: str
    address: str


class SendingAddressesResponse(BaseModel):
    items: list[SendingAddressOut]


@router.get("/sending-addresses", response_model=SendingAddressesResponse)
def list_sending_addresses(
    request: Request,
    response: Response,
    _: object = Depends(require_user),
) -> SendingAddressesResponse:
    user = get_user(request)

    if not _limiter.check(user.user_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many sending-address requests; try again in a minute",
            headers={"Retry-After": "60"},
        )

    # Account identifiers on a possibly-shared machine: no intermediary or
    # browser cache should hold these across logins. Same helper shape as
    # `routes/me.py::_onboarding_no_store`.
    response.headers["Cache-Control"] = "no-store"

    # Deliberately NOT wrapped in a try/except. A per-mailbox failure is handled
    # inside `sending_addresses` by omitting that mailbox; a failure of the
    # enumeration itself means we do not know which mailboxes exist, and
    # returning `items: []` there would be indistinguishable from "you have no
    # sendable mailboxes" — which the client treats as a settled fact. An empty
    # list must only ever mean the question was answered.
    resolved = email_credentials.sending_addresses(user.user_id)

    return SendingAddressesResponse(
        items=[
            SendingAddressOut(source_id=source_id, address=address)
            for source_id, address in resolved.items()
        ]
    )
