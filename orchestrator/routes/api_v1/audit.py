# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Audit list + reverse endpoints for the v1 façade.

Two routes:

* ``GET  /api/v1/audit`` — wraps :func:`actions.audit.get_audit` with
  per-user scoping and an admin-only ``?as_user=`` override.
* ``POST /api/v1/audit/{reverse_token}/reverse`` — wraps
  :func:`actions.reversibility.attempt_reverse` with caller-scoped
  ``user_id`` so bob cannot reverse alice's actions even if he
  somehow learns the token.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from collections import deque
from datetime import datetime
from typing import AsyncGenerator
from typing import Deque

from actions import audit as audit_module
from actions.reversibility import attempt_reverse
from auth import get_user
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi import status
from fastapi.responses import StreamingResponse
from models.api_v1 import AuditEntryDTO
from models.api_v1 import AuditListResponse
from models.api_v1 import AuditReverseResponse
from services.audit_taxonomy import enrich_audit_row

import config

_log = logging.getLogger(__name__)

_AUDIT_STREAM_POLL_SEC = 2.0

router = APIRouter(
    prefix="/api/v1",
    tags=["v1-audit"],
    dependencies=[Depends(require_user)],
)


# Reverse calls share the approvals 30/60s/user budget per the plan
# (§Rate-limits and quotas → "Approvals state-changing endpoints").
# The bucket lives here as a sibling of approvals so the two routers
# can be loaded independently in tests.
_REV_WINDOW_SEC = 60.0
_REV_LIMIT = 30
_reverse_calls: dict[str, Deque[float]] = defaultdict(deque)


def _reverse_rate_check(request: Request) -> None:
    user_id = get_user(request).user_id
    now = time.monotonic()
    bucket = _reverse_calls[user_id]
    while bucket and now - bucket[0] > _REV_WINDOW_SEC:
        bucket.popleft()
    if len(bucket) >= _REV_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many approval changes; try again in a minute",
            headers={"Retry-After": "60"},
        )
    bucket.append(now)


@router.get("/audit", response_model=AuditListResponse)
def list_audit(
    request: Request,
    connector: str | None = Query(None, max_length=128),
    action_type: str | None = Query(None, max_length=128),
    event_type: str | None = Query(None, max_length=128),
    after: datetime | None = Query(None),
    before: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    as_user: str | None = Query(None),
) -> AuditListResponse:
    caller = get_user(request)

    if as_user is not None and caller.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "admin_required"},
        )
    if after is not None and before is not None and after > before:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "invalid_date_range"},
        )

    target_user_id = as_user or caller.user_id

    total = audit_module.count_audit(
        connector=connector,
        action_type=action_type,
        event_type=event_type,
        after=after,
        before=before,
        user_id=target_user_id,
    )
    rows = audit_module.get_audit(
        connector=connector,
        action_type=action_type,
        event_type=event_type,
        after=after,
        before=before,
        user_id=target_user_id,
        limit=limit,
        offset=offset,
    )
    enriched = [AuditEntryDTO.model_validate(enrich_audit_row(r)) for r in rows]
    return AuditListResponse(audit=enriched, total=total, limit=limit, offset=offset)


def _audit_stream_target_user(request: Request, as_user: str | None) -> str:
    caller = get_user(request)
    if as_user is not None and caller.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "admin_required"},
        )
    return as_user or caller.user_id


@router.get("/audit/stream")
async def stream_audit(
    request: Request,
    connector: str | None = Query(None, max_length=128),
    action_type: str | None = Query(None, max_length=128),
    event_type: str | None = Query(None, max_length=128),
    after: datetime | None = Query(None),
    before: datetime | None = Query(None),
    since_id: int = Query(0, ge=0),
    as_user: str | None = Query(None),
) -> StreamingResponse:
    """Poll ``audit_log`` and push new rows as SSE ``audit_entry`` events."""
    if after is not None and before is not None and after > before:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "invalid_date_range"},
        )

    target_user_id = _audit_stream_target_user(request, as_user)

    last_event_header = request.headers.get("last-event-id") or request.headers.get("Last-Event-ID")
    cursor = since_id
    if last_event_header:
        try:
            cursor = max(cursor, int(last_event_header))
        except ValueError:
            pass

    async def generate() -> AsyncGenerator[str, None]:
        nonlocal cursor
        try:
            while True:
                if await request.is_disconnected():
                    break
                rows = audit_module.get_audit_after_id(
                    connector=connector,
                    action_type=action_type,
                    event_type=event_type,
                    after=after,
                    before=before,
                    user_id=target_user_id,
                    after_id=cursor,
                )
                if rows:
                    for row in rows:
                        cursor = int(row["id"])
                        dto = AuditEntryDTO.model_validate(enrich_audit_row(row))
                        payload = json.dumps(dto.model_dump(mode="json"), default=str)
                        yield f"id: {cursor}\nevent: audit_entry\ndata: {payload}\n\n"
                else:
                    yield ": ping\n\n"
                await asyncio.sleep(_AUDIT_STREAM_POLL_SEC)
        except asyncio.CancelledError:
            raise

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/audit/{reverse_token}/reverse",
    response_model=AuditReverseResponse,
    dependencies=[Depends(_reverse_rate_check)],
)
def reverse(reverse_token: str, request: Request) -> AuditReverseResponse:
    caller = get_user(request)

    # Existence + ownership probe — 404 (not 403) on missing-or-other-user
    # to avoid disclosing token validity across users (plan §D5.2).
    ms = config.get_metadata_store()
    try:
        row = ms.fetch_one(
            "SELECT id, reversed_at FROM audit_log WHERE reverse_token = %s AND user_id = %s",
            (reverse_token, caller.user_id),
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("audit.reverse: ownership probe failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "unknown_reverse_token"},
        ) from exc
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "unknown_reverse_token"},
        )
    if row.get("reversed_at") is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "already_reversed"},
        )

    result = attempt_reverse(reverse_token, user_id=caller.user_id)
    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "reverse_failed", "detail": result.error or "unknown"},
        )

    return AuditReverseResponse(reverse_token=reverse_token)
