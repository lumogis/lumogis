# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""Authentication middleware and user context.

Disabled by default (AUTH_ENABLED=false). When enabled, enforces
Bearer JWT tokens and populates request.state.user with a UserContext.
Self-hosted single-user: user_id is always 'default'.
"""

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


async def auth_middleware(request: Request, call_next):
    """No-op when AUTH_ENABLED=false. Enforces Bearer token when true."""
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
