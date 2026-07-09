# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``GET /api/v1/health`` — non-admin per-service health for the web client.

A cheap, cached, non-sensitive projection of the admin stack-status snapshot
(LUM-512). Drives the web client's graceful-degradation banners; the actual
request/response stays the source of truth for live errors. Authed (any user)
but, unlike ``/admin/diagnostics/stack-status``, requires no admin role and
exposes no runtime detail, storage, or model list.
"""

from __future__ import annotations

from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from models.api_v1 import HealthResponse

from services import user_health as user_health_svc

router = APIRouter(
    prefix="/api/v1",
    tags=["v1-health"],
    dependencies=[Depends(require_user)],
)


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={401: {"description": "Unauthenticated"}},
)
def get_health() -> HealthResponse:
    """Return the cached non-sensitive per-service health snapshot."""
    return user_health_svc.get_user_health()
