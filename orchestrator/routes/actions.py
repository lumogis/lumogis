# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""Actions and routines API routes.

GET  /actions                         — registered actions with metadata
GET  /routines                        — list routines with status
POST /routines/{name}/approve         — approve a routine (starts scheduling)
POST /routines/{name}/run             — manual trigger
DELETE /routines/{name}/approve       — revoke approval (stops scheduling)
GET  /audit                           — recent audit log, filterable
POST /audit/{reverse_token}/reverse   — attempt reversal
POST /permissions/{connector}/elevate — explicit routine Do elevation
"""

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

_log = logging.getLogger(__name__)

router = APIRouter(tags=["actions"])

_HARD_LIMITED = frozenset(
    {
        "financial_transaction",
        "mass_communication",
        "permanent_deletion",
        "first_contact",
        "code_commit",
    }
)


class ElevateRequest(BaseModel):
    action_type: str


# ---------------------------------------------------------------------------
# Actions registry
# ---------------------------------------------------------------------------


@router.get("/actions")
def list_actions():
    """Return all registered actions with metadata."""
    from actions.registry import list_actions as _list

    return {"actions": _list(), "total": len(_list())}


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


@router.get("/audit")
def get_audit(
    connector: str | None = Query(None),
    action_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """Return recent audit log entries."""
    from actions.audit import get_audit as _get

    rows = _get(connector=connector, action_type=action_type, limit=limit)
    result = []
    for r in rows:
        result.append(
            {
                "id": r["id"],
                "action_name": r["action_name"],
                "connector": r["connector"],
                "mode": r["mode"],
                "input_summary": r["input_summary"],
                "result_summary": r["result_summary"],
                "reverse_token": str(r["reverse_token"]) if r["reverse_token"] else None,
                "reverse_action": r["reverse_action"],
                "executed_at": r["executed_at"].isoformat() if r["executed_at"] else None,
                "reversed_at": r["reversed_at"].isoformat() if r.get("reversed_at") else None,
            }
        )
    return {"audit": result, "total": len(result)}


@router.post("/audit/{reverse_token}/reverse")
def reverse_action(reverse_token: str):
    """Attempt to reverse a previously executed action."""
    from actions.reversibility import attempt_reverse

    result = attempt_reverse(reverse_token)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    return {"status": "reversed", "output": result.output}


# ---------------------------------------------------------------------------
# Routines
# ---------------------------------------------------------------------------


@router.get("/routines")
def list_routines():
    from services.routines import list_routines as _list

    return {"routines": _list()}


@router.post("/routines/{name}/approve")
def approve_routine(name: str):
    from services.routines import approve_routine as _approve

    ok = _approve(name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Routine {name!r} not found or approval failed")
    return {"status": "approved", "routine": name}


@router.post("/routines/{name}/run")
def run_routine(name: str):
    from services.routines import run_routine as _run

    result = _run(name)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Run failed"))
    return {"status": "ok", "output": result.get("output", "")}


@router.delete("/routines/{name}/approve")
def revoke_routine(name: str):
    from services.routines import revoke_routine as _revoke

    ok = _revoke(name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Routine {name!r} not found")
    return {"status": "revoked", "routine": name}


# ---------------------------------------------------------------------------
# Permission elevation
# ---------------------------------------------------------------------------


@router.post("/permissions/{connector}/elevate")
def elevate_permission(connector: str, body: ElevateRequest):
    """Explicitly elevate an action_type to routine Do for a connector.

    Returns 403 for action_types that can never be elevated (hard limits).
    """
    if body.action_type in _HARD_LIMITED:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Action type {body.action_type!r} can never be elevated to routine Do. "
                f"Hard-limited types: {sorted(_HARD_LIMITED)}"
            ),
        )
    from permissions import elevate_to_routine

    elevate_to_routine(connector, body.action_type)
    return {
        "status": "elevated",
        "connector": connector,
        "action_type": body.action_type,
    }
