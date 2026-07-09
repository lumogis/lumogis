# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Admin resolution API for household biography conflicts (LUM-514)."""

from __future__ import annotations

import logging
from uuid import UUID

from authz import require_admin
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import status
from models.biography_conflict import BiographyConflictListResponse
from models.biography_conflict import ConflictResolution
from models.biography_conflict import ConflictResolutionRequest
from models.biography_conflict import DetectedConflict

from services.biography_conflict_store import ConflictAlreadyClosedError
from services.biography_conflict_store import get_conflict
from services.biography_conflict_store import get_conflict_detail
from services.biography_conflict_store import list_conflicts
from services.biography_conflict_store import resolve_conflict

_log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/biography/conflicts",
    tags=["v1-biography-conflicts"],
)


@router.get("", response_model=BiographyConflictListResponse)
def list_biography_conflicts(
    status_filter: str | None = Query("open", alias="status"),
    _: object = Depends(require_user),
) -> BiographyConflictListResponse:
    conflicts = list_conflicts(status=status_filter)
    return BiographyConflictListResponse(conflicts=conflicts)


@router.get("/{conflict_id}", response_model=DetectedConflict)
def get_biography_conflict(
    conflict_id: UUID,
    _: object = Depends(require_user),
) -> DetectedConflict:
    if get_conflict(conflict_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conflict not found")
    detail = get_conflict_detail(conflict_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conflict not found")
    return detail


@router.post("/{conflict_id}/resolve", response_model=ConflictResolution)
def resolve_biography_conflict(
    conflict_id: UUID,
    body: ConflictResolutionRequest,
    admin=Depends(require_admin),
) -> ConflictResolution:
    try:
        result = resolve_conflict(
            conflict_id,
            body,
            resolved_by=admin.user_id,
        )
    except ValueError as exc:
        _log.warning("invalid biography conflict resolution: %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ConflictAlreadyClosedError as exc:
        _log.warning("biography conflict %s already closed", conflict_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"conflict already {exc.conflict.status}",
        ) from exc

    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conflict not found")
    return result
