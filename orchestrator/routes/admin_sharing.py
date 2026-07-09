# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Admin household-sharing governance routes (LUM-584).

An **admin-only** surface to review and retract household shares on behalf
of the household — distinct from the owner-only unshare every member has
via ``routes/scope.py``. All routes are gated by :func:`authz.require_admin`
(non-admin → 403); the mutating verb additionally attaches
:func:`csrf.require_same_origin` (Bearer + dev-mode bypass, per ADR 016),
mirroring the other admin mutating routers.

Prefix convention follows ``connector_credentials.admin_router`` /
``mcp_tokens.admin_router`` — a dedicated ``APIRouter(prefix="/api/v1/admin/…")``
(``routes/admin.py``'s own router is unprefixed, so it is *not* the home for
a ``/api/v1`` admin route).

Routes
------
* ``GET  /api/v1/admin/shared-items`` — household-wide shared items with
  their **source pk** + owner, so the admin UI can obtain the id the unshare
  route needs (no publish response exposes ``published_from``).
* ``DELETE /api/v1/admin/shared-items/{resource_type}/{resource_id}`` —
  retract a member's share (retract-only, audited). ``resource_id`` is the
  source publish pk.

Domain → HTTP mapping (:mod:`services.admin_unshare`):

* :class:`UnknownResource`   → ``400 unknown_resource_type``
* :class:`SharedItemNotFound`→ ``404 not_found`` (opaque — the same shape
  whether the source never existed or is simply not shared, so the route
  is not an existence oracle over private items)
* :class:`TeardownIncomplete`→ ``500 unshare_incomplete`` (never claims
  success on a partial/unverifiable Qdrant teardown)
* :class:`AuditWriteFailed`  → ``500 audit_write_failed`` (an unaudited
  governance override fails loud)
"""

from __future__ import annotations

import logging

from auth import UserContext
from authz import require_admin
from csrf import require_same_origin
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from models.api_v1 import AdminSharedItem
from models.api_v1 import AdminSharedItemsResponse
from models.api_v1 import AdminUnshareResult

from services import admin_unshare as svc

_log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/shared-items",
    tags=["admin-shared-items"],
    dependencies=[Depends(require_admin)],
)


@router.get("", response_model=AdminSharedItemsResponse)
def list_shared_items() -> AdminSharedItemsResponse:
    """List every household shared item with source pk + owner (admin-only).

    Admin gating is enforced by the router-level ``require_admin`` dependency;
    this handler needs no acting-user context (unlike the delete handler,
    which records the admin as the audit actor).
    """
    items = [AdminSharedItem.model_validate(row) for row in svc.admin_list_shared_items()]
    return AdminSharedItemsResponse(items=items)


@router.delete(
    "/{resource_type}/{resource_id}",
    response_model=AdminUnshareResult,
    dependencies=[Depends(require_same_origin)],
)
def admin_unshare_item(
    resource_type: str,
    resource_id: str,
    user: UserContext = Depends(require_admin),
) -> AdminUnshareResult:
    """Retract another member's household share (admin governance, audited)."""
    try:
        result = svc.admin_unshare(actor=user, resource=resource_type, pk=resource_id)
    except svc.UnknownResource:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unknown_resource_type",
                "message": f"Unknown shareable resource type: {resource_type!r}",
            },
        )
    except svc.SharedItemNotFound:
        # Opaque 404 — identical for "no such source" and "not currently
        # shared" so an admin cannot probe private-item existence by pk.
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": "Item not found or not currently shared.",
            },
        )
    except svc.TeardownIncomplete:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "unshare_incomplete",
                "message": "Couldn't fully unshare — please retry.",
            },
        )
    except svc.AuditWriteFailed:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "audit_write_failed",
                "message": "Action could not be recorded — please retry.",
            },
        )
    return AdminUnshareResult.model_validate(result)
