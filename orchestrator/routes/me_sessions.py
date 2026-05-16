# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Per-user browser session endpoints (LUM-29).

Mounted at ``/api/v1/me/*`` alongside ``routes/me.py``:

* ``GET /api/v1/me/sessions`` — list active device sessions (non-revoked).
* ``DELETE /api/v1/me/sessions/{session_id}`` — revoke one session owned by caller.
* ``POST /api/v1/me/logout-all`` — bump ``token_version`` and revoke all sessions.
"""

from __future__ import annotations

from auth import auth_enabled
from auth import get_user
from auth import invalidate_token_version_cache
from authz import require_user
from csrf import require_same_origin
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi import status
from models.auth import SessionListResponse
from models.auth import SessionRowPublic

from services import auth_sessions as auth_sess

router = APIRouter(
    prefix="/api/v1/me",
    tags=["me-sessions"],
    dependencies=[Depends(require_user)],
)


@router.get("/sessions", response_model=SessionListResponse)
def list_my_sessions(request: Request) -> SessionListResponse:
    if not auth_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="sessions API requires AUTH_ENABLED=true",
        )
    ctx = get_user(request)
    rows = auth_sess.list_active_sessions_for_user(ctx.user_id)
    return SessionListResponse(
        sessions=[
            SessionRowPublic(
                id=r.id,
                device_label=r.device_label,
                created_at=r.created_at,
                last_used_at=r.last_used_at,
                expires_at=r.expires_at,
            )
            for r in rows
        ]
    )


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
    dependencies=[Depends(require_same_origin)],
)
def delete_my_session(session_id: str, request: Request) -> Response:
    if not auth_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="sessions API requires AUTH_ENABLED=true",
        )
    ctx = get_user(request)
    row = auth_sess.revoke_session_for_user(session_id=session_id, user_id=ctx.user_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
    dependencies=[Depends(require_same_origin)],
)
def logout_all_sessions(request: Request) -> Response:
    if not auth_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="sessions API requires AUTH_ENABLED=true",
        )
    ctx = get_user(request)
    auth_sess.bump_token_version_and_revoke_all_sessions(
        user_id=ctx.user_id,
        cascade_actor_user_id=ctx.user_id,
    )
    invalidate_token_version_cache(ctx.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
