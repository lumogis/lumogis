# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Auth for the KG service.

Lumogis is single-user by default and self-hosted. The KG service has THREE
distinct authentication surfaces:

  1. `/webhook`, `/context`, and `/tools/*` — Core-to-KG capability invokes.
     Bearer token guarded by `GRAPH_WEBHOOK_SECRET` (see `routes/tools.py`,
     `routes/webhook.py`, `routes/context.py`). NOT gated by this middleware.
  2. `/mcp` — external MCP clients. Gated **in this module**
     (`auth_middleware`, LUM-915): with `AUTH_ENABLED=true` it needs a Core
     **access** JWT (`MCP_AUTH_TOKEN` is refused); with auth off, the
     `MCP_AUTH_TOKEN` shared secret when it is set, open as `"default"` when
     it is not. The verified caller is carried to the tools in a ContextVar
     (`current_mcp_user`); tools never take a `user_id` argument.
  3. `/health`, `/capabilities`, `/mgm`, `/graph/*`, `/kg/*`, `/codegraph/*` —
     operator and viz routes, reached from the operator's browser on a trusted
     network or via Core's reverse proxy. Unauthenticated by default.
     Operators opt in to JWT auth with `AUTH_ENABLED=true` and
     `AUTH_SECRET=...`; the same access JWT Core mints is then accepted, with
     the same `typ` and `tv` checks (`_verify_token`).

Reachability (recorded in LUM-915): in the household overlay the graph service
is reachable by compose-network siblings only (Caddy routes `/mcp/*` to Core;
community capabilities sit on the isolated egress network); the RC overlay
publishes it on loopback; the release compose has no graph service. The MCP SDK
also answers 421 to a non-loopback `Host` and checks a sent `Origin` against
loopback. That stops browsers and hostname-addressed siblings, but `Host` is
client-set, so it is defence in depth, not authentication.

This module exposes `UserContext`, `get_user`, and an `auth_middleware` that
mirrors Core's behaviour just well enough to keep `request.state.user.user_id`
always populated (defaulting to `"default"` for self-hosted single-user mode).
"""

import hmac
import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

_log = logging.getLogger(__name__)


@dataclass
class UserContext:
    user_id: str = "default"
    is_authenticated: bool = False
    role: str = "admin"
    allows_shared: bool | None = None
    """Role mirror from Core's JWT (Phase 3).

    The KG service does not run its own users table — it trusts Core's
    JWT ``role`` claim. When ``AUTH_ENABLED=false`` we synthesise an
    admin context so the open-by-default dev experience is preserved.
    When ``AUTH_ENABLED=true`` we copy the role straight off the
    decoded token (defaulting to ``"user"`` for tokens minted before
    Phase 2 added the claim).
    """


class TokenCheckUnavailable(Exception):
    """The ``users.token_version`` lookup failed (Postgres down). Maps to 503."""


# The verified caller of the current ``/mcp`` request (LUM-915). Set by
# ``auth_middleware`` around ``call_next`` and read by the graph MCP tools.
_MCP_CALLER: ContextVar[UserContext | None] = ContextVar("lumogis_graph_mcp_caller", default=None)

_mcp_token_warned = False


def current_mcp_user() -> UserContext:
    """Return the authenticated ``/mcp`` caller; fail closed outside the gate."""
    ctx = _MCP_CALLER.get()
    if ctx is None:
        raise PermissionError("no authenticated MCP caller")
    return ctx


def _is_mcp_path(path: str) -> bool:
    """Segment-safe ``/mcp`` match (``/mcpx`` is not MCP)."""
    return path == "/mcp" or path.startswith("/mcp/")


def _secret_matches(presented: str, expected: str) -> bool:
    """Constant-time shared-secret compare on UTF-8 bytes.

    Bytes, not ``str``: ``hmac.compare_digest`` raises ``TypeError`` on a ``str``
    with non-ASCII characters, and Starlette decodes headers as latin-1, so a
    ``Bearer é`` would otherwise be a 500 instead of a 401.
    """
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def _user_context_from_payload(payload: dict) -> UserContext:
    allows_shared = payload.get("allows_shared", True)
    as_flag: bool | None
    if allows_shared is False or allows_shared == 0:
        as_flag = False
    else:
        as_flag = True if "allows_shared" in payload else None

    return UserContext(
        user_id=payload["sub"],
        is_authenticated=True,
        role=payload.get("role", "user"),
        allows_shared=as_flag,
    )


async def _mcp_caller(request: Request) -> UserContext | JSONResponse:
    """Resolve the ``/mcp`` caller, or the 401/503 response that refuses it.

    Branch order mirrors Core's ``_check_mcp_bearer`` (``orchestrator/auth.py``),
    minus ``lmcp_…`` tokens: Core stays the only MCP token authority (ADR 017).

    With auth off the caller is always ``"default"`` (unlike Core, the graph
    does not read ``MCP_DEFAULT_USER_ID``): every auth-off graph path runs as
    ``"default"``, which is where auth-off graph data lives.
    """
    global _mcp_token_warned

    presented = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    shared = os.environ.get("MCP_AUTH_TOKEN", "").strip()

    if os.environ.get("AUTH_ENABLED", "false").lower() == "true":
        if not presented:
            return JSONResponse(status_code=401, content={"error": "missing token"})
        try:
            payload = await run_in_threadpool(_verify_token, presented)
        except TokenCheckUnavailable:
            return JSONResponse(status_code=503, content={"error": "auth check unavailable"})
        if payload:
            return _user_context_from_payload(payload)
        if shared and _secret_matches(presented, shared):
            if not _mcp_token_warned:
                _mcp_token_warned = True
                _log.warning(
                    "KG /mcp: MCP_AUTH_TOKEN presented but AUTH_ENABLED=true; "
                    "only a Core access JWT is accepted"
                )
            return JSONResponse(
                status_code=401,
                content={
                    "error": "MCP_AUTH_TOKEN is not accepted when AUTH_ENABLED=true; "
                    "present a Core access JWT"
                },
            )
        return JSONResponse(status_code=401, content={"error": "invalid token"})

    if not shared:
        return UserContext()
    if not presented or not _secret_matches(presented, shared):
        return JSONResponse(status_code=401, content={"error": "invalid mcp token"})
    return UserContext()


async def auth_middleware(request: Request, call_next):
    """No-op when AUTH_ENABLED=false. Enforces Bearer token when true.

    `/webhook`, `/context`, `/health`, `/capabilities`, and `/tools/*` always
    pass through with a default `UserContext`; their own auth is enforced
    inside the route handler so unauthenticated misconfiguration fails
    closed at the right boundary instead of leaking through here.

    `/mcp` is gated here too (LUM-915): see ``_mcp_caller``. The verified
    caller is set in a ContextVar for the duration of the request and reset in
    ``finally``.

    ``/mgm``, ``/graph/assets/*``, every ``/kg/*`` path and mutating
    ``/graph/*`` / ``/codegraph/*`` calls require ``role == 'admin'`` when
    ``AUTH_ENABLED=true`` (``_requires_admin``). A failed ``token_version``
    lookup is a 503, not a 401.
    """
    path = request.url.path
    if _is_mcp_path(path):
        resolved = await _mcp_caller(request)
        if isinstance(resolved, JSONResponse):
            return resolved
        request.state.user = resolved
        reset = _MCP_CALLER.set(resolved)
        try:
            return await call_next(request)
        finally:
            _MCP_CALLER.reset(reset)

    open_paths = ("/webhook", "/context", "/health", "/capabilities")
    if path.startswith(open_paths) or path.startswith("/tools/"):
        request.state.user = UserContext()
        return await call_next(request)

    if not os.environ.get("AUTH_ENABLED", "false").lower() == "true":
        request.state.user = UserContext()
        return await call_next(request)

    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token:
        return JSONResponse(status_code=401, content={"error": "missing token"})

    try:
        user = await run_in_threadpool(_verify_token, token)
    except TokenCheckUnavailable:
        return JSONResponse(status_code=503, content={"error": "auth check unavailable"})
    if not user:
        return JSONResponse(status_code=401, content={"error": "invalid token"})

    ctx = _user_context_from_payload(user)
    request.state.user = ctx

    if _requires_admin(request) and ctx.role != "admin":
        _log.warning(
            "KG authz: 403 user_id=%s role=%s path=%s",
            ctx.user_id,
            ctx.role,
            request.url.path,
        )
        return JSONResponse(status_code=403, content={"error": "admin role required"})
    return await call_next(request)


def _requires_admin(request: Request) -> bool:
    """Return True for paths that must be admin-only when AUTH_ENABLED=true.

    /mgm and its /graph/assets/* scripts are the operator's graph management UI.
    Every /kg/* path (Core's twins of these routes are all admin-only) and every
    mutating call under /graph/ and /codegraph/ (backfill, reindex, ...) change
    shared graph state. Reads (``GET``/``HEAD``/``OPTIONS``) under /graph/ stay
    open to any authenticated member so standard users can still query the graph.

    This is the role layer. The routes' optional ``X-Graph-Admin-Token`` check
    (``graph/routes.py::_check_admin``, ``routes/graph_admin_routes.py::_require_admin``,
    ``routes/codegraph.py::_require_admin``) is a second layer on top of it.
    """
    path = request.url.path
    if path.startswith(("/mgm", "/graph/assets/")):
        return True
    if path == "/kg" or path.startswith("/kg/"):
        return True
    if path.startswith(("/graph/", "/codegraph/")) and request.method.upper() not in (
        "GET",
        "HEAD",
        "OPTIONS",
    ):
        return True
    return False


def get_user(request: Request) -> UserContext:
    """Convenience: extracts user context set by auth_middleware."""
    return getattr(request.state, "user", UserContext())


# Source of truth: ``ACCESS_TOKEN_TYP`` in Core's ``orchestrator/auth.py``. The KG
# image cannot import Core's module, so the value is repeated here (LUM-885).
_ACCESS_TOKEN_TYP = "access"


def _require_tv_claim() -> bool:
    """Source of truth: ``require_tv_claim`` in ``orchestrator/auth.py``."""
    return os.environ.get("LUMOGIS_REQUIRE_TV_CLAIM", "true").strip().lower() not in (
        "false",
        "0",
        "no",
    )


def _token_version_ok(payload: dict) -> bool:
    """Compare the JWT ``tv`` claim with ``users.token_version`` (LUM-915).

    Mirrors Core's ``jwt_revocation_failure_reason`` /
    ``resolve_current_token_version`` (``orchestrator/auth.py``) with three
    deliberate differences:

    * No cache. Core invalidates its own TTL cache on a bump
      (``invalidate_token_version_cache``) and cannot reach this process, so a
      demotion or sign-out applies on the graph's very next request.
    * A missing ``users`` row rejects. Core treats it as version 0
      (``orchestrator/auth.py:343``); there is no reason to accept a token for a
      user who no longer exists.
    * A failed lookup raises ``TokenCheckUnavailable`` (503, still fail closed).
      Core lets the DB error propagate.
    """
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        return False
    tv = payload.get("tv")
    if tv is None:
        return not _require_tv_claim()
    try:
        tv_claim = int(tv)
    except (TypeError, ValueError):
        return False
    import config

    try:
        row = config.get_metadata_store().fetch_one(
            "SELECT token_version FROM users WHERE id = %s", (sub,)
        )
    except Exception as exc:
        _log.warning("KG auth: token_version lookup failed exc_type=%s", type(exc).__name__)
        raise TokenCheckUnavailable from None
    if row is None:
        return False
    return int(row["token_version"]) == tv_claim


def _verify_token(token: str) -> dict | None:
    """Decode a Core **access** JWT using AUTH_SECRET. Returns payload dict or None.

    The one verifier for every authenticated graph path (``/mgm``,
    ``/graph/*``, ``/kg/*``, ``/codegraph/*``, ``/mcp``). A future path (for
    example a LUM-960 cookie path) must call this, not decode again. Checks:
    signature, expiry, ``typ == "access"`` (LUM-885, security audit #2 AUTH-002:
    a refresh JWT, or a token without ``typ``, is rejected even when signed
    with the same secret) and ``tv`` against ``users.token_version`` (LUM-915).

    Blocks on Postgres, so async callers use ``run_in_threadpool``. Raises
    ``TokenCheckUnavailable`` when the ``tv`` lookup fails.

    Core's optional ``sid`` revocation check
    (``LUMOGIS_REQUIRE_SID_REVOCATION_CHECK``, default off,
    ``orchestrator/auth.py`` ``jwt_revocation_failure_reason``) is not mirrored;
    mirror it here if Core turns it on by default.
    """
    secret = os.environ.get("AUTH_SECRET", "")
    if not secret:
        _log.warning("AUTH_ENABLED=true but AUTH_SECRET is not set on KG service")
        return None
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    if not isinstance(payload, dict) or payload.get("typ") != _ACCESS_TOKEN_TYP:
        _log.debug("KG auth: rejected token without typ=access")
        return None
    if not _token_version_ok(payload):
        _log.debug("KG auth: rejected token with stale or missing tv")
        return None
    return payload
