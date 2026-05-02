# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Request correlation middleware (chunk: structured_audit_logging).

A single FastAPI middleware that:

  1. Echoes the incoming ``X-Request-ID`` header if present, otherwise
     generates a new ``uuid.uuid4().hex``.
  2. Binds ``request_id`` into ``structlog.contextvars`` for the
     lifetime of the request, so every structured log line emitted in
     the same async task carries the same id.
  3. Stashes the live ``Request`` object in a module-level
     :class:`~contextvars.ContextVar` so the
     ``_bind_request_user`` structlog processor can read
     ``request.state.user.user_id`` / ``request.state.mcp_token_id`` /
     ``request.state.mcp_user_id`` *at log time* — i.e. after
     ``auth_middleware`` has populated them but before the response
     leaves the server.
  4. Sets ``X-Request-ID`` on the outgoing response.

Middleware ordering (see plan D4a):

    app.middleware("http")(correlation_middleware)   # registered FIRST
    app.middleware("http")(auth_middleware)          # registered LAST → outermost

Starlette wraps in reverse-of-registration, so ``auth_middleware``
becomes the outermost layer. ``correlation_middleware`` therefore runs
*inside* auth's ``await call_next(request)``, which is exactly when
``request.state.user`` is populated. The tradeoff is that log lines
emitted by ``auth_middleware``'s own early-return 401 paths do NOT
carry ``request_id``; this is documented in
``docs/structured-logging.md`` as an accepted scope choice for this
chunk.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Optional

import structlog
from fastapi import Request

# Module-level context var holding the live ``Request`` for the current
# async task. The ``_bind_request_user`` processor in
# ``logging_config.py`` reads from this so logs emitted from any depth
# inside the request handler can pick up ``user_id`` / ``mcp_token_id``
# without the caller having to pass them explicitly.
_REQUEST_CTXVAR: ContextVar[Optional[Request]] = ContextVar(
    "lumogis_current_request", default=None
)


async def correlation_middleware(request: Request, call_next):
    """Bind ``request_id`` + current request into contextvars for the request lifetime."""
    incoming = request.headers.get("X-Request-ID")
    request_id = incoming.strip() if incoming and incoming.strip() else uuid.uuid4().hex
    request.state.request_id = request_id

    req_token = _REQUEST_CTXVAR.set(request)
    structlog.contextvars.bind_contextvars(request_id=request_id)
    try:
        response = await call_next(request)
    finally:
        _REQUEST_CTXVAR.reset(req_token)
        structlog.contextvars.unbind_contextvars("request_id")

    # Echo the request id back so clients (and reverse proxies) can
    # cross-reference logs without re-reading the request body.
    response.headers["X-Request-ID"] = request_id
    return response
