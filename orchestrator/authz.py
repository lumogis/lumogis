# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""FastAPI role-gating dependencies.

Single responsibility: turn ``request.state.user`` (set by
``auth.auth_middleware``) into 401/403/200 outcomes. **No DB lookups** —
the access JWT already carries the role claim, and Phase 1's
:func:`auth.auth_middleware` validated it. Per-request DB hits would
nullify the cheap-stateless-JWT property the family-LAN model relies on.

Bi-state behaviour (see ADR ``family_lan_multi_user``):

* ``AUTH_ENABLED=false`` (single-user dev) — every dependency is a no-op.
  The synthesised :class:`UserContext("default", role="admin")` passes both
  ``require_user`` and ``require_admin`` checks. This preserves the
  current open-by-default dev experience.
* ``AUTH_ENABLED=true`` (family LAN) — checks the JWT-derived
  ``UserContext`` and returns 401 (not authenticated) or 403 (insufficient
  role) per ADR §12.

Status code contract
--------------------
* ``401 Unauthorized`` — ``AUTH_ENABLED=true`` and the bearer token was
  missing, malformed, or expired. The middleware short-circuits these
  before they reach a route, so dependencies will see this case only
  when the route lives behind a path the middleware exempts (e.g. the
  ``auth_router`` bypass list).
* ``403 Forbidden`` — authenticated user is below the required role.
  Logged ``WARNING`` with the user_id and the route path so admins can
  spot probe attempts.
* ``200`` — through.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request, status

from auth import UserContext, auth_enabled, get_user

_log = logging.getLogger(__name__)


def require_user(request: Request) -> UserContext:
    """Pass through any authenticated user (admin or user role).

    No-op when ``AUTH_ENABLED=false``. Returned :class:`UserContext` is
    the same one ``request.state.user`` carries — handlers can use the
    return value or read ``get_user(request)`` directly.
    """
    if not auth_enabled():
        return get_user(request)
    ctx = get_user(request)
    if not ctx.is_authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not authenticated",
        )
    return ctx


def require_admin(request: Request) -> UserContext:
    """Require ``role == 'admin'``.

    No-op when ``AUTH_ENABLED=false`` (dev mode is admin-equivalent).
    Otherwise: 401 if unauthenticated, 403 if authenticated but not
    admin.
    """
    if not auth_enabled():
        return get_user(request)
    ctx = get_user(request)
    if not ctx.is_authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not authenticated",
        )
    if ctx.role != "admin":
        _log.warning(
            "authz: 403 user_id=%s role=%s path=%s",
            ctx.user_id,
            ctx.role,
            request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin role required",
        )
    return ctx
