# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Minimal auth shim for the KG service.

Lumogis is single-user and self-hosted. The KG service has THREE distinct
authentication surfaces:

  1. `/webhook` and `/context` — Core-to-KG calls. Bearer token guarded by
     `GRAPH_WEBHOOK_SECRET`. Implemented in `routes/webhook.py` and
     `routes/context.py` directly, NOT here.
  2. `/mcp/*` — external MCP clients (Thunderbolt, etc.). Bearer token
     guarded by `MCP_AUTH_TOKEN`. Implemented in middleware in `main.py`.
  3. `/health`, `/capabilities`, `/mgm`, `/api/graph/*`, `/api/viz/*` —
     intended to be reached either directly from the operator's browser
     on a trusted internal network or via Core's reverse proxy. They are
     unauthenticated by default. Operators can opt in to JWT auth by
     setting `AUTH_ENABLED=true` and `AUTH_SECRET=...` on the KG
     container, in which case the same JWT Core mints will be accepted.

This module exposes the bare minimum the copied admin/viz routes need:
`UserContext`, `get_user`, and an `auth_middleware` that mirrors Core's
behaviour just well enough to keep `request.state.user.user_id` always
populated (defaulting to `"default"` for self-hosted single-user mode).
"""

import logging
import os
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse

_log = logging.getLogger(__name__)


@dataclass
class UserContext:
    user_id: str = "default"
    is_authenticated: bool = False
    role: str = "admin"
    """Role mirror from Core's JWT (Phase 3).

    The KG service does not run its own users table — it trusts Core's
    JWT ``role`` claim. When ``AUTH_ENABLED=false`` we synthesise an
    admin context so the open-by-default dev experience is preserved.
    When ``AUTH_ENABLED=true`` we copy the role straight off the
    decoded token (defaulting to ``"user"`` for tokens minted before
    Phase 2 added the claim).
    """


async def auth_middleware(request: Request, call_next):
    """No-op when AUTH_ENABLED=false. Enforces Bearer token when true.

    `/webhook`, `/context`, `/health`, and `/capabilities` always pass
    through with a default `UserContext`; their own auth is enforced
    inside the route handler so unauthenticated misconfiguration fails
    closed at the right boundary instead of leaking through here.

    `/mcp/*` is gated independently (in `main.py`) by `MCP_AUTH_TOKEN`.

    Phase 3: ``/mgm`` and write paths under ``/api/graph/*`` /
    ``/api/viz/*`` require ``role == 'admin'`` when ``AUTH_ENABLED=true``.
    The role mirror lives on :class:`UserContext`; this middleware
    rejects non-admin callers before they reach the handler.
    """
    open_paths = ("/webhook", "/context", "/health", "/capabilities")
    if request.url.path.startswith(open_paths) or request.url.path.startswith("/mcp"):
        request.state.user = UserContext()
        return await call_next(request)

    if not os.environ.get("AUTH_ENABLED", "false").lower() == "true":
        request.state.user = UserContext()
        return await call_next(request)

    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token:
        return JSONResponse(status_code=401, content={"error": "missing token"})

    user = _verify_token(token)
    if not user:
        return JSONResponse(status_code=401, content={"error": "invalid token"})

    ctx = UserContext(
        user_id=user["sub"],
        is_authenticated=True,
        role=user.get("role", "user"),
    )
    request.state.user = ctx

    if _requires_admin(request) and ctx.role != "admin":
        _log.warning(
            "KG authz: 403 user_id=%s role=%s path=%s",
            ctx.user_id,
            ctx.role,
            request.url.path,
        )
        return JSONResponse(
            status_code=403, content={"error": "admin role required"}
        )
    return await call_next(request)


def _requires_admin(request: Request) -> bool:
    """Return True for paths that must be admin-only when AUTH_ENABLED=true.

    /mgm is the operator's graph management UI. Writes under
    /api/graph and /api/viz mutate the shared graph state. Reads
    (``GET``) on those same prefixes remain accessible to any
    authenticated user so standard users can still query the graph.
    """
    path = request.url.path
    if path.startswith("/mgm"):
        return True
    if path.startswith(("/api/graph", "/api/viz")) and request.method.upper() not in (
        "GET",
        "HEAD",
        "OPTIONS",
    ):
        return True
    return False


def get_user(request: Request) -> UserContext:
    """Convenience: extracts user context set by auth_middleware."""
    return getattr(request.state, "user", UserContext())


def _verify_token(token: str) -> dict | None:
    """Decode a JWT using AUTH_SECRET. Returns payload dict or None.

    Imports `jwt` lazily so the KG service can run without PyJWT
    installed when `AUTH_ENABLED=false` (which is the default).
    """
    secret = os.environ.get("AUTH_SECRET", "")
    if not secret:
        _log.warning("AUTH_ENABLED=true but AUTH_SECRET is not set on KG service")
        return None
    try:
        import jwt  # type: ignore[import-not-found]
    except ImportError:
        _log.error(
            "AUTH_ENABLED=true on KG service but PyJWT is not installed — "
            "rejecting all authenticated requests"
        )
        return None
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
