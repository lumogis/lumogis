# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Approvals + connector-mode + elevation endpoints for the v1 façade.

Surface area (per plan §API routes → Approvals / audit):

* ``GET  /api/v1/approvals/pending``        — unioned pending list
* ``POST /api/v1/approvals/connector/{c}/mode`` — flip a connector's ASK/DO
* ``POST /api/v1/approvals/elevate``        — promote an action_type to routine

State-changing routes are gated by an in-process token-bucket
(``30/min/user``) modelled on :func:`routes.auth._rate_check`. Read-only
``GET /pending`` is unmetered.

Audit attribution writes go through :func:`actions.audit.write_audit`
with ``connector="__permissions_change__"`` so admin-shell filters can
discover them via ``action_type=set_mode``. The reserved prefix is
non-reversible (no ``reverse_token`` is minted) — toggling back is a
fresh ``set_connector_mode`` call which itself audits.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque
from typing import Deque

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

import config
from auth import get_user
from authz import require_user
from actions import audit as audit_module
from actions import registry as actions_registry
from actions.executor import is_hard_limited
from models.actions import AuditEntry
from models.api_v1 import (
    ConnectorModeRequest,
    ConnectorModeResponse,
    DeniedActionItem,
    ElevateRequest,
    ElevateResponse,
    ElevationCandidateItem,
    PendingApprovalsResponse,
)
from permissions import elevate_to_routine, set_connector_mode
from services.api_v1_risk import elevation_eligible, risk_tier_for

_log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/approvals",
    tags=["v1-approvals"],
    dependencies=[Depends(require_user)],
)


# ---------------------------------------------------------------------------
# In-process token-bucket: 30 state-changing approvals calls / 60s / user.
# Mirrors the shape of `routes/auth.py::_rate_check` so the operational
# story is identical (per-process; lost on restart; no Redis).
# ---------------------------------------------------------------------------

_APPROVAL_WINDOW_SEC = 60.0
_APPROVAL_LIMIT = 30
_approval_calls: dict[str, Deque[float]] = defaultdict(deque)


def _approvals_rate_check(request: Request) -> None:
    user_id = get_user(request).user_id
    now = time.monotonic()
    bucket = _approval_calls[user_id]
    while bucket and now - bucket[0] > _APPROVAL_WINDOW_SEC:
        bucket.popleft()
    if len(bucket) >= _APPROVAL_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many approval changes; try again in a minute",
            headers={"Retry-After": "60"},
        )
    bucket.append(now)


# ---------------------------------------------------------------------------
# GET /api/v1/approvals/pending
# ---------------------------------------------------------------------------


@router.get("/pending", response_model=PendingApprovalsResponse)
def pending(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    as_user: str | None = Query(None),
) -> PendingApprovalsResponse:
    caller = get_user(request)
    target_user_id = _resolve_as_user(caller, as_user)

    ms = config.get_metadata_store()

    denied_rows = _safe_fetch_all(
        ms,
        "SELECT id, connector, action_type, input_summary, created_at "
        "FROM action_log "
        "WHERE allowed = FALSE AND user_id = %s "
        "ORDER BY created_at DESC LIMIT %s",
        (target_user_id, limit),
    )

    # SCOPE-EXEMPT: routine_do_tracking has no `scope` column (per-user
    # routine elevation tracking — migration 016).
    elev_rows = _safe_fetch_all(
        ms,
        "SELECT connector, action_type, approval_count "
        "FROM routine_do_tracking "
        # SCOPE-EXEMPT: routine_do_tracking is per-user, no scope column.
        "WHERE user_id = %s AND approval_count >= 15 "
        "      AND auto_approved = FALSE AND edit_count = 0 "
        "ORDER BY approval_count DESC LIMIT %s",
        (target_user_id, limit),
    )

    pending: list = []
    for r in denied_rows:
        action_type = r["action_type"]
        eligible = elevation_eligible(action_type)
        suggested = _suggested_action(
            ms,
            target_user_id=target_user_id,
            connector=r["connector"],
            action_type=action_type,
        )
        pending.append(
            DeniedActionItem(
                action_log_id=r["id"],
                connector=r["connector"],
                action_type=action_type,
                risk_tier=risk_tier_for(action_type),
                input_summary=r.get("input_summary"),
                occurred_at=r["created_at"],
                elevation_eligible=eligible,
                suggested_action=suggested,
            )
        )

    for r in elev_rows:
        action_type = r["action_type"]
        pending.append(
            ElevationCandidateItem(
                connector=r["connector"],
                action_type=action_type,
                approval_count=int(r["approval_count"]),
                risk_tier=risk_tier_for(action_type),
                elevation_eligible=elevation_eligible(action_type),
            )
        )

    return PendingApprovalsResponse(pending=pending[:limit])


def _suggested_action(
    ms,
    *,
    target_user_id: str,
    connector: str,
    action_type: str,
) -> str:
    """Decision tree from plan §Data contracts → DeniedActionItem."""
    if is_hard_limited(action_type):
        return "explain_only"
    # SCOPE-EXEMPT: routine_do_tracking is per-user, no scope column.
    row = _safe_fetch_one(
        ms,
        "SELECT approval_count, auto_approved, edit_count "
        "FROM routine_do_tracking "
        "WHERE user_id = %s AND connector = %s AND action_type = %s",
        (target_user_id, connector, action_type),
    )
    if (
        row is not None
        and int(row.get("approval_count") or 0) >= 15
        and not row.get("auto_approved")
        and int(row.get("edit_count") or 0) == 0
    ):
        return "elevate_action_type"
    return "set_connector_do"


def _resolve_as_user(caller, as_user: str | None) -> str:
    """Apply admin-only ``?as_user=...`` rules.

    Plan §API routes → Approvals: non-admin requests with ``as_user`` set
    are rejected with 403 to avoid silent self-scoping that would mask
    privilege errors.
    """
    if as_user is None:
        return caller.user_id
    if caller.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "admin_required"},
        )
    return as_user


# ---------------------------------------------------------------------------
# POST /api/v1/approvals/connector/{connector}/mode
# ---------------------------------------------------------------------------


@router.post(
    "/connector/{connector}/mode",
    response_model=ConnectorModeResponse,
    dependencies=[Depends(_approvals_rate_check)],
)
def set_mode(
    connector: str,
    body: ConnectorModeRequest,
    request: Request,
) -> ConnectorModeResponse:
    caller = get_user(request)

    actions = [a for a in actions_registry.list_actions() if a["connector"] == connector]
    if not actions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "unknown_connector"},
        )

    hard = [a for a in actions if is_hard_limited(a["action_type"])]
    if len(hard) == len(actions):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "hard_limited_connector"},
        )

    try:
        set_connector_mode(user_id=caller.user_id, connector=connector, mode=body.mode)
    except ValueError as exc:  # invalid mode — Pydantic already gates,
        # but defence-in-depth keeps the contract honest.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_mode", "detail": str(exc)},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        _log.exception(
            "approvals.set_mode failed user=%s connector=%s mode=%s",
            caller.user_id, connector, body.mode,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "internal_error"},
        ) from exc

    audit_module.write_audit(
        AuditEntry(
            action_name=f"__permissions_change__.{connector}",
            connector="__permissions_change__",
            mode="ask",
            input_summary=json.dumps(
                {"new_mode": body.mode, "changed_by": caller.user_id}
            ),
            result_summary=json.dumps({"ok": True}),
            user_id=caller.user_id,
        )
    )

    return ConnectorModeResponse(connector=connector, mode=body.mode)


# ---------------------------------------------------------------------------
# POST /api/v1/approvals/elevate
# ---------------------------------------------------------------------------


@router.post(
    "/elevate",
    response_model=ElevateResponse,
    dependencies=[Depends(_approvals_rate_check)],
)
def elevate(body: ElevateRequest, request: Request) -> ElevateResponse:
    caller = get_user(request)

    if is_hard_limited(body.action_type):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "hard_limited_action"},
        )

    matches = [
        a
        for a in actions_registry.list_actions()
        if a["connector"] == body.connector and a["action_type"] == body.action_type
    ]
    if not matches:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "unknown_action"},
        )

    try:
        elevate_to_routine(
            user_id=caller.user_id,
            connector=body.connector,
            action_type=body.action_type,
        )
    except Exception as exc:  # noqa: BLE001
        _log.exception(
            "approvals.elevate failed user=%s connector=%s action_type=%s",
            caller.user_id, body.connector, body.action_type,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "internal_error"},
        ) from exc

    return ElevateResponse(connector=body.connector, action_type=body.action_type)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_fetch_all(ms, sql: str, params) -> list[dict]:
    try:
        return list(ms.fetch_all(sql, params) or [])
    except Exception as exc:  # noqa: BLE001
        _log.warning("approvals: fetch_all failed: %s", exc)
        return []


def _safe_fetch_one(ms, sql: str, params) -> dict | None:
    try:
        return ms.fetch_one(sql, params)
    except Exception as exc:  # noqa: BLE001
        _log.warning("approvals: fetch_one failed: %s", exc)
        return None
