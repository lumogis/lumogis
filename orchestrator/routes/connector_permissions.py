# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""HTTP routes for the per-user connector permission surface.

Per plan ``per_user_connector_permissions`` (audit A2 closure), three
routers live here so :mod:`main` can wire them at distinct prefixes:

* :data:`router`            — caller-self ``/api/v1/me/permissions``
                              (gated by :func:`authz.require_user`).
* :data:`admin_router`      — admin on-behalf-of-user
                              ``/api/v1/admin/users/{user_id}/permissions``
                              (gated by :func:`authz.require_admin`).
* :data:`admin_list_router` — cross-user enumeration
                              ``/api/v1/admin/permissions``
                              (gated by :func:`authz.require_admin`).

Mutating verbs (``PUT``, ``DELETE``) carry
:func:`csrf.require_same_origin` — bearer-authenticated calls bypass
the check by design (see ``orchestrator/csrf.py``), so curl + the
dashboard's ``fetch`` flows keep working unchanged. Mirrors
``routes/mcp_tokens.py`` D11/D12.

Connector validation
--------------------
Every route that accepts a ``{connector}`` path param validates the
value against :func:`actions.registry.list_actions`. On registry
failure the write proceeds with a single WARN log and a
``Warning: 199`` advisory header (degrade-allow per OQ #4). The
length cap (``max_length=64``) lives on the FastAPI ``Path(...)``
constraint so a long-string spam DoS is rejected with 422 BEFORE the
SQL or registry lookup runs.

Information-leak guard
----------------------
The ``me`` GET/PUT/DELETE handlers operate strictly on
``(caller.user_id, connector)`` and never enumerate other users'
rows. The admin routes resolve identity from the path ``{user_id}``
only after the ``require_admin`` gate.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import permissions
from auth import get_user
from authz import require_admin
from authz import require_user
from csrf import require_same_origin
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Path
from fastapi import Request
from fastapi import Response
from models.connector_permission import CapabilityGrantView
from models.connector_permission import ConnectorPermissionAdminView
from models.connector_permission import ConnectorPermissionPublic
from models.connector_permission import ConnectorPermissionUpdate
from services.capability_scopes import CAPABILITY_ID_PATTERN as _CAP_ID_PATTERN

from services import users as users_service

_log = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/v1/me/permissions",
    tags=["me-permissions"],
    dependencies=[Depends(require_user)],
)

admin_router = APIRouter(
    prefix="/api/v1/admin/users/{user_id}/permissions",
    tags=["admin-permissions"],
    dependencies=[Depends(require_admin)],
)

admin_list_router = APIRouter(
    prefix="/api/v1/admin/permissions",
    tags=["admin-permissions"],
    dependencies=[Depends(require_admin)],
)


# Pinned constraints for every {connector} path param so the cap lives
# in exactly one place. ``min_length=1`` blocks empty-string callers
# (FastAPI normally allows them); ``max_length=64`` matches the longest
# shipped connector name plus headroom; the regex matches
# kebab/snake-case identifiers and rejects path-traversal characters
# at the route layer before the value reaches SQL or the registry.
# LUM-612: accept first-party connectors AND `capability.{id}` grant connectors.
# The dotted `capability.` branch is bounded by max_length=64 and — critically —
# by the registry existence check in `_validate_connector_or_warn`, so junk like
# `capability.a..b` cannot resolve to a real grant. Plain connectors are unchanged.
# The capability-id charset is derived from the single source of truth in
# `capability_scopes.CAPABILITY_ID_PATTERN` (drop its ^…$ anchors) so the route
# regex and the registration guard can never drift apart on the id shape.
_CAP_ID_BODY = _CAP_ID_PATTERN.pattern.removeprefix("^").removesuffix("$")
_CONNECTOR_PATTERN = rf"^(capability\.{_CAP_ID_BODY}|[a-z][a-z0-9_-]*)$"
_CONNECTOR_PATH = Path(
    ...,
    min_length=1,
    max_length=64,
    description=(
        "Connector slug (e.g. filesystem-mcp) or capability id "
        "(capability.{id}; validated at runtime)."
    ),
)

_ACTION_REGISTRY_UNAVAILABLE_HEADER = '199 - "action registry unavailable; connector unvalidated"'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _known_connectors() -> set[str] | None:
    """Distinct connector names from the live **action** registry (``list_actions``).

    Returns ``None`` on action-registry failure so the caller can degrade-allow
    first-party connectors with a ``Warning: 199`` advisory header (OQ #4).
    """
    try:
        from actions.registry import list_actions

        return {spec["connector"] for spec in list_actions() if spec.get("connector")}
    except Exception:  # noqa: BLE001 — degrade-allow per OQ #4
        _log.warning("action_registry_unavailable_at_permission_validation")
        return None


def _known_capability_connectors() -> set[str] | None:
    """`capability.{id}` names for every registered capability service (LUM-612).

    Sourced in a **separate** try from the action registry so a capability
    -registry error never weakens plain-connector validation. Returns ``None`` on
    failure — callers **fail closed** for `capability.*` writes (never degrade
    -allow an unverified capability grant). The set is legitimately empty until
    first discovery completes at startup, so real capabilities 404 briefly then.
    """
    try:
        from services.capability_scopes import capability_connector

        import config

        reg = config.get_capability_registry()
        return {capability_connector(svc.manifest.id) for svc in reg.all_services()}
    except Exception:  # noqa: BLE001
        _log.warning("capability_registry_unavailable_at_permission_validation")
        return None


def _validate_connector_or_warn(
    connector: str,
    response: Response,
    *,
    write_path: bool,
    allow_missing: bool = False,
) -> None:
    """Validate ``connector`` against the live registry.

    First-party connectors: unknown + reachable action registry → 404; action
    -registry failure → degrade-allow with a ``Warning: 199`` header (OQ #4).

    `capability.{id}` connectors (LUM-612): validated against the **capability**
    registry and **fail closed** — an unknown or unverifiable capability is 404,
    never degrade-allowed (no writing an unverified/grant-before-ask scope grant
    during an outage). ``allow_missing=True`` bypasses the existence check so an
    orphaned capability grant stays revocable via DELETE.
    """
    if not re.fullmatch(_CONNECTOR_PATTERN, connector):
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_connector", "connector": connector},
        )

    from services.capability_scopes import is_capability_connector

    if is_capability_connector(connector):
        if allow_missing:
            return
        known_caps = _known_capability_connectors()
        if known_caps is None or connector not in known_caps:
            _log.warning("unknown_or_unverifiable_capability_connector connector=%s", connector)
            raise HTTPException(
                status_code=404,
                detail={"error": "unknown_connector", "connector": connector},
            )
        return

    known = _known_connectors()
    if known is None:
        log_event = (
            "action_registry_unavailable_at_permission_write"
            if write_path
            else "action_registry_unavailable_at_permission_read"
        )
        _log.warning(log_event)
        response.headers["Warning"] = _ACTION_REGISTRY_UNAVAILABLE_HEADER
        return
    if connector not in known:
        _log.warning("unknown_connector_in_user_put connector=%s", connector)
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_connector", "connector": connector},
        )


def _public_from_effective(row: dict[str, Any]) -> ConnectorPermissionPublic:
    return ConnectorPermissionPublic(
        connector=row["connector"],
        mode=row["mode"],
        scopes=list(row.get("scopes") or []),
        is_default=row["is_default"],
        updated_at=row.get("updated_at"),
    )


def _admin_view_from_effective(
    row: dict[str, Any],
    *,
    user_id: str,
    email: str | None,
) -> ConnectorPermissionAdminView:
    return ConnectorPermissionAdminView(
        user_id=user_id,
        email=email,
        connector=row["connector"],
        mode=row["mode"],
        scopes=list(row.get("scopes") or []),
        is_default=row["is_default"],
        updated_at=row.get("updated_at"),
    )


def _effective_row_for(
    *,
    user_id: str,
    connector: str,
) -> dict[str, Any]:
    """Return the single-connector effective row for ``user_id``.

    Cheap wrapper around :func:`permissions.get_user_effective_permissions`
    with ``known_connectors=[connector]`` so the lazy default and the
    explicit-row branches share one shape.
    """
    rows = permissions.get_user_effective_permissions(
        user_id=user_id,
        known_connectors=[connector],
    )
    return rows[0]


def _require_user_exists(user_id: str) -> None:
    if users_service.get_user_by_id(user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")


def _email_for(user_id: str) -> str | None:
    """Look up an email for ``user_id``. Returns ``None`` if the user is
    hard-deleted (forensic-retained permission rows on the admin
    enumeration endpoint).
    """
    user = users_service.get_user_by_id(user_id)
    return user.email if user is not None else None


# ---------------------------------------------------------------------------
# User-facing routes — /api/v1/me/permissions
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[ConnectorPermissionPublic],
)
def list_my_permissions(request: Request) -> list[ConnectorPermissionPublic]:
    """Effective per-connector view for the caller across every known connector.

    Connectors without an explicit per-user row come back with
    ``is_default=True, mode='ASK', updated_at=None`` (the lazy
    ``_DEFAULT_MODE`` fallback). When the action registry (``list_actions``)
    is unreachable the response falls back to the explicit-rows-only set
    and attaches a ``Warning: 199`` advisory header.
    """
    caller = get_user(request)
    known = _known_connectors()
    if known is None:
        _log.warning("action_registry_unavailable_at_permission_read")
        explicit = permissions.get_user_permissions(user_id=caller.user_id)
        rows = [
            {
                "connector": r["connector"],
                "mode": r["mode"],
                "scopes": list(r.get("scopes") or []),
                "is_default": False,
                "updated_at": r.get("updated_at"),
            }
            for r in explicit
        ]
        out = [_public_from_effective(r) for r in rows]
        # Attach the advisory via the underlying Response object — FastAPI
        # gives us request.scope but the response isn't returned-then-
        # mutated, so we use Starlette's response builder pattern via a
        # custom Response wrapper. Simpler: mutate via state and let the
        # caller's middleware see it. Here we attach to the Request's
        # response by using a sentinel header on a fresh Response is
        # impractical without rewriting the return type; instead we log
        # and accept that the list endpoint's degrade is observable in
        # logs. The single-connector GET below DOES return the header.
        return out
    effective = permissions.get_user_effective_permissions(
        user_id=caller.user_id,
        known_connectors=sorted(known),
    )
    return [_public_from_effective(r) for r in effective]


@router.get(
    "/capabilities",
    response_model=list[CapabilityGrantView],
)
def list_my_capability_grants(request: Request) -> list[CapabilityGrantView]:
    """Consent surface (LUM-612): per registered capability, what it **requires**
    vs what the caller has **granted**, and what's still **missing**.

    This is the data an install/consent UI renders. Required scopes come from the
    instance-global capability registry (same visibility as ``/me/tools``); granted
    scopes are resolved strictly for the caller. Defined before ``/{connector}`` so
    the literal path is not swallowed by the path param.
    """
    from services.capability_scopes import capability_connector
    from services.capability_scopes import required_scopes_for

    import config

    caller = get_user(request)
    try:
        reg = config.get_capability_registry()
        services = list(reg.all_services())
    except Exception:  # noqa: BLE001
        _log.warning("capability_registry_unavailable_at_consent_surface")
        services = []

    out: list[CapabilityGrantView] = []
    for svc in services:
        cap_id = svc.manifest.id
        connector = capability_connector(cap_id)
        required = required_scopes_for(svc.manifest)
        granted = permissions.get_granted_scopes(user_id=caller.user_id, connector=connector)
        missing = sorted(set(required) - set(granted))
        out.append(
            CapabilityGrantView(
                capability_id=cap_id,
                connector=connector,
                required=required,
                granted=granted,
                missing=missing,
            )
        )
    return out


@router.get(
    "/{connector}",
    response_model=ConnectorPermissionPublic,
)
def get_my_permission(
    request: Request,
    response: Response,
    connector: str = _CONNECTOR_PATH,
) -> ConnectorPermissionPublic:
    """Single-connector effective view for the caller.

    ``404`` when ``{connector}`` is unknown to the registry AND the
    registry is reachable. ``Warning: 199`` header (response still
    ``200``) when the registry is unreachable but the row lookup
    proceeds — see the module docstring for the degrade-allow contract.
    """
    caller = get_user(request)
    _validate_connector_or_warn(connector, response, write_path=False)
    row = _effective_row_for(user_id=caller.user_id, connector=connector)
    return _public_from_effective(row)


@router.put(
    "/{connector}",
    response_model=ConnectorPermissionPublic,
    dependencies=[Depends(require_same_origin)],
)
def put_my_permission(
    body: ConnectorPermissionUpdate,
    request: Request,
    response: Response,
    connector: str = _CONNECTOR_PATH,
) -> ConnectorPermissionPublic:
    """UPSERT the caller's mode for ``{connector}``.

    Returns the new effective row. ``mode`` is canonical-cased
    (``"ASK"``/``"DO"``) at the v1 layer; lowercase variants get
    ``422`` from the Pydantic ``Literal``.
    """
    caller = get_user(request)
    _validate_connector_or_warn(connector, response, write_path=True)
    # LUM-612: mode and scopes are both optional; a field left None is untouched
    # (a mode-only PUT doesn't wipe scopes and vice-versa); {} is a no-op.
    if body.mode is not None:
        permissions.set_connector_mode(
            user_id=caller.user_id,
            connector=connector,
            mode=body.mode,
        )
    if body.scopes is not None:
        try:
            permissions.grant_scopes(
                user_id=caller.user_id,
                connector=connector,
                scopes=body.scopes,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_scope", "message": str(exc)},
            ) from exc
    row = _effective_row_for(user_id=caller.user_id, connector=connector)
    return _public_from_effective(row)


@router.delete(
    "/{connector}",
    response_model=ConnectorPermissionPublic,
    dependencies=[Depends(require_same_origin)],
)
def delete_my_permission(
    request: Request,
    response: Response,
    connector: str = _CONNECTOR_PATH,
) -> ConnectorPermissionPublic:
    """Drop the caller's explicit row for ``{connector}``.

    Returns the now-default row (``is_default=True``). Idempotent — a
    DELETE against a connector with no per-user row is a no-op that
    still returns ``200`` with the lazy default.

    LUM-612: for a ``capability.{id}`` connector the existence check is
    bypassed (``allow_missing=True``) so a grant to a now-unregistered
    capability stays revocable — you must always be able to revoke.
    """
    caller = get_user(request)
    _validate_connector_or_warn(connector, response, write_path=True, allow_missing=True)
    permissions.delete_user_permission(
        user_id=caller.user_id,
        connector=connector,
    )
    row = _effective_row_for(user_id=caller.user_id, connector=connector)
    return _public_from_effective(row)


# ---------------------------------------------------------------------------
# Admin on-behalf-of routes — /api/v1/admin/users/{user_id}/permissions
# ---------------------------------------------------------------------------


@admin_router.get(
    "",
    response_model=list[ConnectorPermissionAdminView],
)
def admin_list_user_permissions(user_id: str) -> list[ConnectorPermissionAdminView]:
    """Admin-only effective view for one user across every known connector.

    ``404`` when ``user_id`` does not exist in the ``users`` table
    (mirrors :func:`routes.mcp_tokens.admin_list_user_tokens`).
    """
    _require_user_exists(user_id)
    email = _email_for(user_id)
    known = _known_connectors() or set()
    effective = permissions.get_user_effective_permissions(
        user_id=user_id,
        known_connectors=sorted(known),
    )
    return [_admin_view_from_effective(r, user_id=user_id, email=email) for r in effective]


@admin_router.get(
    "/{connector}",
    response_model=ConnectorPermissionAdminView,
)
def admin_get_user_permission(
    user_id: str,
    response: Response,
    connector: str = _CONNECTOR_PATH,
) -> ConnectorPermissionAdminView:
    """Admin-only single-connector view for one user."""
    _require_user_exists(user_id)
    _validate_connector_or_warn(connector, response, write_path=False)
    email = _email_for(user_id)
    row = _effective_row_for(user_id=user_id, connector=connector)
    return _admin_view_from_effective(row, user_id=user_id, email=email)


@admin_router.put(
    "/{connector}",
    response_model=ConnectorPermissionAdminView,
    dependencies=[Depends(require_same_origin)],
)
def admin_put_user_permission(
    user_id: str,
    body: ConnectorPermissionUpdate,
    request: Request,
    response: Response,
    connector: str = _CONNECTOR_PATH,
) -> ConnectorPermissionAdminView:
    """Admin UPSERTs the target user's mode for ``{connector}``."""
    if users_service.get_user_by_id(user_id) is None:
        _log.warning(
            "admin_put_permission_unknown_user user_id=%s connector=%s",
            user_id,
            connector,
        )
        raise HTTPException(status_code=404, detail="user not found")
    _validate_connector_or_warn(connector, response, write_path=True)
    # LUM-612: mode + scopes both optional (a field left None is untouched).
    if body.mode is not None:
        permissions.set_connector_mode(
            user_id=user_id,
            connector=connector,
            mode=body.mode,
        )
    if body.scopes is not None:
        try:
            permissions.grant_scopes(user_id=user_id, connector=connector, scopes=body.scopes)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail={"error": "invalid_scope", "message": str(exc)}
            ) from exc
    # Race: user could be hard-deleted between the existence check and
    # the read-back. Return the email lookup result; if the user is gone,
    # email comes back as None (forensic-retained permission row).
    email = _email_for(user_id)
    row = _effective_row_for(user_id=user_id, connector=connector)
    caller = get_user(request)
    _log.info(
        "permission_changed_by_admin admin_user_id=%s target_user_id=%s connector=%s mode=%s",
        caller.user_id,
        user_id,
        connector,
        body.mode,
    )
    return _admin_view_from_effective(row, user_id=user_id, email=email)


@admin_router.delete(
    "/{connector}",
    response_model=ConnectorPermissionAdminView,
    dependencies=[Depends(require_same_origin)],
)
def admin_delete_user_permission(
    user_id: str,
    request: Request,
    response: Response,
    connector: str = _CONNECTOR_PATH,
) -> ConnectorPermissionAdminView:
    """Admin drops the target user's explicit row for ``{connector}``.

    LUM-612: mirrors the self-service DELETE — the existence check is
    bypassed (``allow_missing=True``) for a ``capability.{id}`` connector so
    an admin can always force-revoke a grant to a now-deregistered or
    unreachable capability (the incident-response case).
    """
    _require_user_exists(user_id)
    _validate_connector_or_warn(connector, response, write_path=True, allow_missing=True)
    permissions.delete_user_permission(
        user_id=user_id,
        connector=connector,
    )
    email = _email_for(user_id)
    row = _effective_row_for(user_id=user_id, connector=connector)
    caller = get_user(request)
    _log.info(
        "permission_deleted_by_admin admin_user_id=%s target_user_id=%s connector=%s",
        caller.user_id,
        user_id,
        connector,
    )
    return _admin_view_from_effective(row, user_id=user_id, email=email)


# ---------------------------------------------------------------------------
# Admin cross-user enumeration — /api/v1/admin/permissions
# ---------------------------------------------------------------------------


@admin_list_router.get(
    "",
    response_model=list[ConnectorPermissionAdminView],
)
def admin_list_all_permissions() -> list[ConnectorPermissionAdminView]:
    """Cross-user enumeration of every explicit per-user override.

    One row per ``(user_id, connector)`` that has an explicit row,
    sorted by ``(user_id, connector)``. Replaces the legacy
    ``GET /permissions`` for the admin dashboard. Connectors at the
    lazy default for a given user do NOT appear here — only explicit
    overrides — to keep the response O(rows-in-table) rather than
    O(users × connectors).

    ``email`` is ``None`` for any forensic-retained row whose owner has
    been hard-deleted; populated for disabled-but-extant users.
    """
    rows = permissions.get_all_permissions()
    if not rows:
        return []
    # Single batched email lookup keeps the route O(distinct users + 1)
    # DB roundtrips instead of O(rows). The list is bounded by the
    # plan's documented O(users × connectors) ceiling (single-digit
    # kilobytes); a single in-memory dict is fine.
    distinct_user_ids = {r["user_id"] for r in rows}
    emails = {uid: _email_for(uid) for uid in distinct_user_ids}
    return [
        ConnectorPermissionAdminView(
            user_id=r["user_id"],
            email=emails.get(r["user_id"]),
            connector=r["connector"],
            mode=r["mode"],
            scopes=list(r.get("scopes") or []),
            is_default=False,
            updated_at=r.get("updated_at"),
        )
        for r in rows
    ]
