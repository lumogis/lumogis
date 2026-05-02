# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Admin-only user management endpoints.

Mounted at ``/api/v1/admin/users`` per the family-LAN multi-user plan §11.
Strictly admin-only — every route carries ``Depends(require_admin)``.

Routes
------
* ``POST   /api/v1/admin/users``              — create a user (admin or user role).
* ``GET    /api/v1/admin/users``              — list all users with operational fields.
* ``POST   /api/v1/admin/users/{id}/password`` — set a user's password (clears refresh jti).
* ``PATCH  /api/v1/admin/users/{id}``       — update role and/or disabled flag.
* ``DELETE /api/v1/admin/users/{id}``        — hard-delete a user.

Safety invariants enforced here (not in :mod:`services.users`)
--------------------------------------------------------------
1. **Cannot delete the last active admin.** Returns 400.
2. **Cannot demote the last active admin to ``user``.** Returns 400.
3. **Cannot disable the last active admin.** Returns 400.
4. **Cannot self-delete or self-disable.** Returns 400 — admins must
   demote themselves first or rely on another admin. This avoids an
   admin locking themselves out mid-session.
5. **Email uniqueness** enforced by the underlying
   :func:`services.users.create_user` (returns 409 on duplicate).
6. **Disable / role-demote / delete** all clear the active refresh jti
   in :func:`services.users.set_disabled` / :func:`delete_user`, so the
   user cannot rotate refresh tokens after the change. Access tokens
   remain valid until TTL expiry — documented limitation in plan §12.
"""

from __future__ import annotations

import logging

from auth import get_user
from authz import require_admin
from csrf import require_same_origin
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi import status
from models.auth import AckOk
from models.auth import AdminUserPasswordResetRequest
from models.auth import UserAdminView
from models.auth import UserCreateRequest
from models.auth import UserPatchRequest
from models.user_export import ArchiveInventoryEntry
from models.user_export import ImportPlan
from models.user_export import ImportReceipt
from models.user_export import ImportRefused
from models.user_export import ImportRequest

from services import user_export as user_export_service
from services import users as users_service

_log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/users",
    tags=["admin-users"],
    dependencies=[Depends(require_admin)],
)


def _to_admin_view(internal) -> UserAdminView:
    """Project an :class:`InternalUser` to the admin-visible response."""
    return UserAdminView(
        id=internal.id,
        email=internal.email,
        role=internal.role,
        disabled=internal.disabled,
        created_at=internal.created_at,
        last_login_at=internal.last_login_at,
    )


@router.post(
    "",
    response_model=UserAdminView,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_same_origin)],
)
def create_user(body: UserCreateRequest) -> UserAdminView:
    """Create a new account. Returns 409 on duplicate email."""
    try:
        user = users_service.create_user(
            email=str(body.email),
            password=body.password,
            role=body.role,
        )
    except ValueError as exc:
        # services.users.create_user raises this on duplicate email
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    _log.info("admin: created user_id=%s email=%s role=%s", user.id, user.email, user.role)
    return _to_admin_view(user)


@router.get("", response_model=list[UserAdminView])
def list_users() -> list[UserAdminView]:
    """List every user. Order: oldest-first (creation order)."""
    return [_to_admin_view(u) for u in users_service.list_users()]


@router.post(
    "/{user_id}/password",
    response_model=AckOk,
    dependencies=[Depends(require_same_origin)],
)
def reset_user_password(
    user_id: str,
    body: AdminUserPasswordResetRequest,
    request: Request,
) -> AckOk:
    """Set ``user_id``'s password. Allowed for disabled accounts (login stays blocked).

    Clears the target's ``refresh_token_jti`` so existing refresh cookies for
    that user stop working.
    """
    try:
        users_service.admin_reset_user_password(user_id, body.new_password)
    except users_service.PasswordPolicyViolationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError:
        raise HTTPException(status_code=404, detail="user not found") from None

    caller = get_user(request)
    _log.info(
        "admin: password reset target_user_id=%s by=%s",
        user_id,
        caller.user_id,
    )
    return AckOk()


@router.patch(
    "/{user_id}",
    response_model=UserAdminView,
    dependencies=[Depends(require_same_origin)],
)
def patch_user(user_id: str, body: UserPatchRequest, request: Request) -> UserAdminView:
    """Update ``role`` and/or ``disabled``. Either field may be omitted.

    Refuses changes that would leave zero active admins, and refuses
    self-disable to avoid locking the calling admin out mid-session.
    """
    target = users_service.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")

    if body.role is None and body.disabled is None:
        raise HTTPException(
            status_code=400,
            detail="at least one of {role, disabled} required",
        )

    caller = get_user(request)

    new_role = body.role if body.role is not None else target.role
    new_disabled = body.disabled if body.disabled is not None else target.disabled

    will_be_active_admin = (new_role == "admin") and (new_disabled is False)
    is_currently_last_admin = (
        target.role == "admin" and not target.disabled and users_service.count_admins() == 1
    )

    if is_currently_last_admin and not will_be_active_admin:
        raise HTTPException(
            status_code=400,
            detail="refusing to demote or disable the last active admin",
        )

    if body.disabled is True and target.id == caller.user_id:
        raise HTTPException(
            status_code=400,
            detail="refusing to self-disable; ask another admin",
        )

    if body.role is not None and body.role != target.role:
        users_service.update_role(user_id, body.role)
    if body.disabled is not None and body.disabled != target.disabled:
        # Thread ``by_admin_user_id`` so the cascade-revoke audit emitted
        # by ``set_disabled`` (per plan ``mcp_token_user_map`` D7/D14)
        # records the acting admin instead of the disabled user.
        users_service.set_disabled(
            user_id,
            body.disabled,
            by_admin_user_id=caller.user_id,
        )

    updated = users_service.get_user_by_id(user_id)
    if updated is None:
        # Should not happen — covered defensively to keep mypy + the tests honest
        raise HTTPException(status_code=500, detail="user vanished after update")

    _log.info(
        "admin: patched user_id=%s role=%s disabled=%s by=%s",
        updated.id,
        updated.role,
        updated.disabled,
        caller.user_id,
    )
    return _to_admin_view(updated)


@router.delete(
    "/{user_id}",
    dependencies=[Depends(require_same_origin)],
)
def delete_user(user_id: str, request: Request) -> dict:
    """Hard-delete a user. Returns 400 if it would leave zero admins."""
    target = users_service.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")

    caller = get_user(request)
    is_last_active_admin = (
        target.role == "admin" and not target.disabled and users_service.count_admins() == 1
    )
    if is_last_active_admin:
        # Check first — most informative when the sole admin tries to
        # self-delete (the self-delete check below is generic).
        raise HTTPException(
            status_code=400,
            detail="refusing to delete the last active admin",
        )

    if target.id == caller.user_id:
        raise HTTPException(
            status_code=400,
            detail="refusing to self-delete; ask another admin",
        )

    users_service.delete_user(user_id)
    _log.info("admin: deleted user_id=%s by=%s", user_id, caller.user_id)
    return {"ok": True}


# ─── Per-user import endpoints (plan: per_user_backup_export) ───────────────
#
# Mounted under a sibling prefix so /api/v1/admin/users/* keeps its
# resource-oriented URL space and the import endpoints don't have to
# fake a noun like "imports" inside the user namespace. ``main.py``
# imports both `router` and `imports_router` (added below) and
# registers them separately.


imports_router = APIRouter(
    prefix="/api/v1/admin/user-imports",
    tags=["admin-user-imports"],
    dependencies=[Depends(require_admin)],
)


# Maps the structured ``ImportRefused.refusal_reason`` to an HTTP
# status code. Centralised so the contract is greppable from a single
# place; matches the table in the plan §"Import refusal contract".
_REFUSAL_TO_STATUS: dict[str, int] = {
    # Use the integer literal so newer + older Starlette installs both
    # work (HTTP_413_CONTENT_TOO_LARGE replaced HTTP_413_REQUEST_ENTITY_TOO_LARGE).
    "archive_too_large": 413,
    "archive_integrity_failed": status.HTTP_400_BAD_REQUEST,
    "archive_unsafe_entry_names": status.HTTP_400_BAD_REQUEST,
    "manifest_invalid": status.HTTP_400_BAD_REQUEST,
    "missing_user_record": status.HTTP_400_BAD_REQUEST,
    "manifest_section_count_mismatch": status.HTTP_400_BAD_REQUEST,
    "missing_sections": status.HTTP_400_BAD_REQUEST,
    "unsupported_format_version": status.HTTP_400_BAD_REQUEST,
    "forbidden_path": status.HTTP_403_FORBIDDEN,
    "email_exists": status.HTTP_409_CONFLICT,
    "uuid_collision_on_parent_table": status.HTTP_409_CONFLICT,
}


def _refusal_to_http(exc: ImportRefused) -> HTTPException:
    code = _REFUSAL_TO_STATUS.get(exc.refusal_reason, status.HTTP_400_BAD_REQUEST)
    return HTTPException(
        status_code=code,
        detail={"refusal_reason": exc.refusal_reason, "payload": exc.payload},
    )


@imports_router.post(
    "",
    dependencies=[Depends(require_same_origin)],
)
def create_user_import(
    body: ImportRequest,
    request: Request,
    response: Response,
) -> ImportReceipt | ImportPlan:
    """Import an archive — ``dry_run=true`` returns an :class:`ImportPlan`
    without writing; ``dry_run=false`` mints a fresh user and returns
    an :class:`ImportReceipt` with HTTP ``201 Created`` plus a
    ``Location: /api/v1/admin/users/{new_user_id}`` header pointing to
    the canonical resource for the freshly-minted account.

    Dry-run preserves ``200 OK`` (it is non-mutating — no resource was
    created, so 201 + Location would be a lie).

    Refusals carry ``refusal_reason`` + structured ``payload`` in the
    HTTP detail body (mapped via ``_REFUSAL_TO_STATUS``); see the plan's
    "Import refusal contract" table for the full reason→status mapping.
    """
    caller = get_user(request)
    try:
        if body.dry_run:
            plan = user_export_service.dry_run_import(
                body.archive_path,
                str(body.new_user.email),
            )
            return plan
        receipt = user_export_service.import_user(
            archive_path=body.archive_path,
            new_user_email=str(body.new_user.email),
            new_user_password=body.new_user.password,
            new_user_role=body.new_user.role,
        )
        _log.info(
            "admin: imported archive=%s new_user_id=%s by=%s",
            body.archive_path,
            receipt.new_user_id,
            caller.user_id,
        )
        # 201 + Location: the import minted a brand-new ``users`` row;
        # the canonical resource for that row is the
        # ``/api/v1/admin/users/{id}`` endpoint registered above. Using
        # ``response.status_code = 201`` (rather than declaring it on
        # the decorator) keeps the dry-run branch on 200 — the contract
        # depends on the body shape.
        response.status_code = status.HTTP_201_CREATED
        response.headers["Location"] = f"/api/v1/admin/users/{receipt.new_user_id}"
        return receipt
    except ImportRefused as exc:
        raise _refusal_to_http(exc) from exc


@imports_router.get(
    "",
    response_model=list[ArchiveInventoryEntry],
)
def list_user_imports() -> list[ArchiveInventoryEntry]:
    """Inventory of every archive under ``${USER_EXPORT_DIR}/*/``.

    Read-only; opens each zip just long enough to parse its
    ``manifest.json``. Bad archives are surfaced via ``manifest_status``
    rather than excluded — operators need to see them to clean up.
    """
    return user_export_service.list_archives()
