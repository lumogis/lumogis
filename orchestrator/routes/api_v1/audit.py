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

import logging
import time
from collections import defaultdict
from collections import deque
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
from models.api_v1 import AuditEntryDTO
from models.api_v1 import AuditListResponse
from models.api_v1 import AuditReverseResponse

import config

_log = logging.getLogger(__name__)

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
    limit: int = Query(50, ge=1, le=200),
    as_user: str | None = Query(None),
) -> AuditListResponse:
    caller = get_user(request)

    if as_user is not None and caller.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "admin_required"},
        )
    target_user_id = as_user or caller.user_id

    rows = audit_module.get_audit(
        connector=connector,
        action_type=action_type,
        user_id=target_user_id,
        limit=limit,
    )
    return AuditListResponse(audit=[AuditEntryDTO.model_validate(r) for r in rows])


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
