# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Authentication middleware and user context.

Disabled by default (AUTH_ENABLED=false). When enabled, enforces
Bearer JWT tokens and populates request.state.user with a UserContext.
Self-hosted single-user: user_id is always 'default'.
"""

import hmac
import logging
import os
from dataclasses import dataclass

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse

_log = logging.getLogger(__name__)


@dataclass
class UserContext:
    user_id: str = "default"
    is_authenticated: bool = False


def _check_mcp_bearer(request: Request) -> JSONResponse | None:
    """Gate /mcp/* requests on MCP_AUTH_TOKEN when set.

    Returns a 401 JSONResponse to short-circuit the request, or None to
    let it pass through. When MCP_AUTH_TOKEN is unset, all /mcp/* requests
    pass through (single-user local default — the documented behaviour).

    Comparison uses hmac.compare_digest for timing-safe string equality so
    a token-prefix attacker cannot probe valid bytes via response timing.
    Lives in this middleware (rather than the mounted MCP Starlette app)
    because layering middleware onto a FastAPI-mounted Starlette sub-app
    is fragile and order-dependent.
    """
    expected = os.environ.get("MCP_AUTH_TOKEN", "").strip()
    if not expected:
        return None
    presented = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not presented or not hmac.compare_digest(presented, expected):
        return JSONResponse(status_code=401, content={"error": "invalid mcp token"})
    return None


async def auth_middleware(request: Request, call_next):
    """No-op when AUTH_ENABLED=false. Enforces Bearer token when true.

    /mcp/* is gated independently by MCP_AUTH_TOKEN regardless of
    AUTH_ENABLED, because the MCP surface is for external clients and
    needs its own opt-in token even when the dashboard runs unauthenticated
    on localhost.
    """
    if request.url.path.startswith("/mcp"):
        rejection = _check_mcp_bearer(request)
        if rejection is not None:
            return rejection
        # MCP surface is single-user local; downstream handlers expect a
        # user context to exist on request.state.
        request.state.user = UserContext()
        return await call_next(request)

    if not os.environ.get("AUTH_ENABLED", "false").lower() == "true":
        request.state.user = UserContext()
        return await call_next(request)

    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token:
        return JSONResponse(status_code=401, content={"error": "missing token"})

    user = verify_token(token)
    if not user:
        return JSONResponse(status_code=401, content={"error": "invalid token"})

    request.state.user = UserContext(user_id=user["sub"], is_authenticated=True)
    return await call_next(request)


def get_user(request: Request) -> UserContext:
    """Convenience: extracts user context set by auth_middleware."""
    return getattr(request.state, "user", UserContext())


def verify_token(token: str) -> dict | None:
    """Decode a JWT using AUTH_SECRET. Returns payload dict or None."""
    secret = os.environ.get("AUTH_SECRET", "")
    if not secret:
        _log.warning("AUTH_ENABLED=true but AUTH_SECRET is not set")
        return None
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
