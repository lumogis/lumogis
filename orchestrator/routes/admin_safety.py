# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Admin safety-playground routes (LUM-141) — ``/api/v1/admin/safety/*``.

Admin-only (``require_admin``); mutating verbs attach ``require_same_origin``
(mirrors ``routes/mcp_tokens.py``). The suite runs against the live PURE
detection primitives (no persistence, no LLM) — see
:mod:`services.safety_playground`.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

from authz import require_admin
from csrf import require_same_origin
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from models.safety_playground import InjectionVector
from models.safety_playground import SafetyCaseList
from models.safety_playground import SafetyProbeRequest
from models.safety_playground import SafetyProbeResult
from models.safety_playground import SafetySuiteResult

from services import safety_playground as svc

router = APIRouter(
    prefix="/api/v1/admin/safety",
    tags=["admin-safety"],
    dependencies=[Depends(require_admin)],
)


def _ensure_enabled() -> None:
    if not svc.is_safety_playground_enabled():
        raise HTTPException(status_code=404, detail="safety_playground_disabled")


@router.get("/cases", response_model=SafetyCaseList)
def list_safety_cases() -> SafetyCaseList:
    """List the static suite (name/vector/expected/known_gap) for the UI."""

    _ensure_enabled()
    return svc.list_cases()


@router.post(
    "/run",
    response_model=SafetySuiteResult,
    dependencies=[Depends(require_same_origin)],
)
def run_safety_suite() -> SafetySuiteResult:
    """Run the full injection suite against the live defences (dry-run)."""

    _ensure_enabled()
    ran_at = datetime.now(timezone.utc).isoformat()
    return svc.run_injection_suite(ran_at=ran_at)


@router.post(
    "/probe",
    response_model=SafetyProbeResult,
    dependencies=[Depends(require_same_origin)],
)
def probe_safety(body: SafetyProbeRequest) -> SafetyProbeResult:
    """Run one ad-hoc payload against a chosen vector."""

    _ensure_enabled()
    if body.vector is InjectionVector.ACTION_EXECUTION:
        if not body.action_type:
            raise HTTPException(status_code=422, detail="action_type required for action_execution")
    elif not body.payload:
        raise HTTPException(status_code=422, detail="payload required for this vector")
    return svc.run_probe(
        vector=body.vector,
        payload=body.payload,
        action_type=body.action_type,
        expected=body.expected,
    )
