# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Per-user export and inventory routes.

Mounted at ``/api/v1/me`` per the per-user backup export plan §"API
routes". Strictly authenticated — every route carries
``Depends(require_user)``. Admins can additionally export on behalf of
another user by passing ``target_user_id`` in the request body
(mirrors the ``POST /ingest`` admin-on-behalf precedent in
``routes/data.py`` — body field, not query param; see plan F2 fix).

Routes
------
* ``POST /api/v1/me/export`` — build and stream the user's own archive.
* ``GET  /api/v1/me/data-inventory`` — read-only enumeration of the
  per-user Postgres section row counts (no Qdrant / FalkorDB hit).
* ``POST /api/v1/me/password`` — change the caller's password (auth-on only).

D11 wiring
----------
``Depends(require_same_origin)`` is attached for forward-compatibility
with cookie-authenticated browser sessions. Bearer-authenticated calls
(the only auth flow shipped today — see plan §D11 + ``orchestrator/
csrf.py``) bypass the check by design; the dependency becomes the
active CSRF defence the moment the cookie-session work lands.
"""

from __future__ import annotations

import io
import logging

from auth import auth_enabled
from auth import get_user
from authz import require_user
from csrf import require_same_origin
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from fastapi.responses import StreamingResponse
from models.api_v1 import MeLlmProvidersResponse
from models.api_v1 import MeNotificationsResponse
from models.api_v1 import MeToolsResponse
from models.auth import AckOk
from models.auth import MePasswordChangeRequest
from models.user_export import ExportRequest
from models.user_export import SectionSummary

from services import me_llm_providers as me_llm_providers_svc
from services import me_notifications as me_notifications_svc
from services import me_tools_catalog
from services import user_export as user_export_service
from services import users as users_service

_log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/me",
    tags=["me"],
    dependencies=[Depends(require_user)],
)


@router.post(
    "/export",
    dependencies=[Depends(require_same_origin)],
)
def export_self(
    body: ExportRequest | None,
    request: Request,
) -> StreamingResponse:
    """Build the caller's per-user archive and stream it back as ``application/zip``.

    Body is optional: ``{}`` means self-export. Setting
    ``target_user_id`` to a non-self id requires ``role == 'admin'`` —
    non-admins get 403. Admins targeting themselves are equivalent to
    omitting the field.
    """
    caller = get_user(request)
    target_id = body.target_user_id if body else None
    if target_id and target_id != caller.user_id:
        if caller.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="admin role required to export another user",
            )
        # Admin-on-behalf: 404 when the target user does not exist.
        # Without this gate the export would silently produce an empty
        # archive (every per-user table SELECT returns 0 rows for an
        # unknown id), which is impossible to distinguish from a real
        # but data-empty user. Self-export skips this check by
        # construction — the caller is authenticated, so they exist.
        if users_service.get_user_by_id(target_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "user not found", "target_user_id": target_id},
            )
        effective_id = target_id
    else:
        effective_id = caller.user_id

    try:
        archive_bytes, filename = user_export_service.export_user(effective_id)
    except RuntimeError as exc:
        # Currently only raised on the size-cap path; map to 413
        # (literal — Starlette renamed the constant in newer versions).
        raise HTTPException(
            status_code=413,
            detail=str(exc),
        ) from exc

    return StreamingResponse(
        io.BytesIO(archive_bytes),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(archive_bytes)),
        },
    )


@router.get(
    "/data-inventory",
    response_model=list[SectionSummary],
)
def data_inventory(request: Request) -> list[SectionSummary]:
    """Per-Postgres-table row counts for the caller's own data.

    No Qdrant or FalkorDB I/O. Cheap enough to call from a dashboard
    "preview-before-export" tile.
    """
    caller = get_user(request)
    return user_export_service.enumerate_user_data_sections(caller.user_id)


@router.get(
    "/tools",
    response_model=MeToolsResponse,
    summary="Unified tool catalog (read-only)",
)
def me_tools_catalog_view(request: Request) -> MeToolsResponse:
    """Observational list of tools Core knows about (LLM, MCP, capabilities, actions).

    Does **not** invoke tools, grant permissions, or return secrets or raw
    JSON Schemas. Rows mirror :func:`services.unified_tools.build_tool_catalog_for_user`
    including ``permission_mode`` (``ask`` / ``do`` / ``blocked`` / ``unknown``)
    from :func:`permissions.get_connector_mode` where a tool maps to a connector.
    """
    caller = get_user(request)
    return me_tools_catalog.build_me_tools_response(caller.user_id)


@router.get(
    "/llm-providers",
    response_model=MeLlmProvidersResponse,
    summary="LLM provider credential status (read-only)",
)
def me_llm_providers_view(request: Request) -> MeLlmProvidersResponse:
    """Curated status of cloud LLM API credentials (per tier / env fallback).

    Does **not** return ciphertext, decrypted keys, or env secret values.
    See :func:`services.me_llm_providers.build_me_llm_providers_response`.
    """
    caller = get_user(request)
    return me_llm_providers_svc.build_me_llm_providers_response(caller.user_id)


@router.get(
    "/notifications",
    response_model=MeNotificationsResponse,
    summary="Notification channels status (read-only)",
)
def me_notifications_view(request: Request) -> MeNotificationsResponse:
    """Curated ntfy + Web Push status for the current user.

    Does **not** return credential payloads, tokens, or subscription endpoints.
    See :func:`services.me_notifications.build_me_notifications_response`.
    """
    caller = get_user(request)
    return me_notifications_svc.build_me_notifications_response(caller.user_id)


@router.post(
    "/password",
    response_model=AckOk,
    summary="Change password (self-service)",
    dependencies=[Depends(require_same_origin)],
)
def change_my_password(body: MePasswordChangeRequest, request: Request) -> AckOk:
    """Verify the current password, set a new hash, clear ``refresh_token_jti``.

    Unavailable when ``AUTH_ENABLED=false``. Wrong current password → 403 with
    a generic ``invalid credentials`` detail (no account enumeration). Policy
    violations → 400. Existing refresh cookies stop working; access JWTs live
    until TTL expiry.
    """
    if not auth_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="password change is unavailable in single-user dev mode",
        )
    caller = get_user(request)
    try:
        users_service.change_own_password(
            caller.user_id,
            body.current_password,
            body.new_password,
        )
    except users_service.WrongCurrentPasswordError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid credentials",
        ) from None
    except users_service.PasswordPolicyViolationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="user not found"
        ) from None
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="account disabled",
        ) from None

    _log.info("me: password changed user_id=%s", caller.user_id)
    return AckOk()
