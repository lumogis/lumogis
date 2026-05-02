# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Authentication middleware and user context.

Bi-state behaviour (see ADR ``family_lan_multi_user``):

* ``AUTH_ENABLED=false`` (default) — single-user dev. No login required;
  every request gets ``UserContext("default", role="admin")``.
* ``AUTH_ENABLED=true`` — family LAN. Bearer JWT required; the JWT
  carries ``sub`` (user id) and ``role`` (``admin`` | ``user``). No
  anonymous fallback.

The ``/mcp/*`` surface keeps its independent ``MCP_AUTH_TOKEN`` gate — it
is for external MCP clients and runs whether ``AUTH_ENABLED`` is on or
off. When the legacy MCP token is presented (no JWT), the request is
attributed to a configured admin user via ``MCP_DEFAULT_USER_ID``
(resolved by ``mcp_server.py`` — Phase 3).
"""

from __future__ import annotations

import hmac
import logging
import os
import time
from dataclasses import dataclass
from typing import Literal

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse

_log = logging.getLogger(__name__)

Role = Literal["admin", "user"]

# In dev mode (`AUTH_ENABLED=false`) we synthesise this user on every request
# so handlers always have something on `request.state.user`. The literal
# "default" remains the documented dev-mode user_id; production (true mode)
# uses uuid4-hex ids minted by the `users` table.
_DEV_USER_ID = "default"


@dataclass
class UserContext:
    user_id: str = _DEV_USER_ID
    is_authenticated: bool = False
    role: Role = "admin"


def auth_enabled() -> bool:
    return os.environ.get("AUTH_ENABLED", "false").strip().lower() == "true"


def access_token_ttl_seconds() -> int:
    raw = os.environ.get("ACCESS_TOKEN_TTL_SECONDS", "900")
    try:
        return max(60, int(raw))
    except ValueError:
        return 900


def refresh_token_ttl_seconds() -> int:
    raw = os.environ.get("REFRESH_TOKEN_TTL_SECONDS", str(30 * 24 * 3600))
    try:
        return max(3600, int(raw))
    except ValueError:
        return 30 * 24 * 3600


def _access_secret() -> str:
    return os.environ.get("AUTH_SECRET", "").strip()


def _refresh_secret() -> str:
    """Refresh-token signing secret.

    Resolution order, first non-empty wins:

    1. ``LUMOGIS_JWT_REFRESH_SECRET`` — Lumogis-only, lets operators keep
       a separate secret from LibreChat. **Preferred for new installs.**
    2. ``JWT_REFRESH_SECRET`` — historical name, also used by LibreChat.
       Kept for backward-compatibility so single-secret deployments
       continue to work.
    3. ``AUTH_SECRET`` — dev-mode fallback only. The entrypoint refuses
       to boot in family-LAN mode if no refresh secret is set, so this
       branch is never reached in production.
    """
    for var in ("LUMOGIS_JWT_REFRESH_SECRET", "JWT_REFRESH_SECRET"):
        s = os.environ.get(var, "").strip()
        if s:
            return s
    return os.environ.get("AUTH_SECRET", "").strip()


def mint_access_token(user_id: str, role: Role) -> str:
    """Mint an HS256 access JWT signed with ``AUTH_SECRET``."""
    secret = _access_secret()
    if not secret:
        raise RuntimeError("AUTH_SECRET is not set; cannot mint access token")
    now = int(time.time())
    payload = {
        "sub": user_id,
        "role": role,
        "iat": now,
        "exp": now + access_token_ttl_seconds(),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def mint_refresh_token(user_id: str, jti: str) -> str:
    """Mint an HS256 refresh JWT (see :func:`_refresh_secret` for the
    secret resolution order)."""
    secret = _refresh_secret()
    if not secret:
        raise RuntimeError(
            "no refresh secret available — set LUMOGIS_JWT_REFRESH_SECRET "
            "(preferred) or JWT_REFRESH_SECRET"
        )
    now = int(time.time())
    payload = {
        "sub": user_id,
        "jti": jti,
        "iat": now,
        "exp": now + refresh_token_ttl_seconds(),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_token(token: str) -> dict | None:
    """Decode an access JWT signed with ``AUTH_SECRET``.

    Returns the payload dict (containing at least ``sub``; ``role`` may be
    absent in legacy tokens — callers default to ``user``) or ``None`` on
    any verification failure.
    """
    secret = _access_secret()
    if not secret:
        _log.warning("AUTH_ENABLED=true but AUTH_SECRET is not set")
        return None
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None


def verify_refresh_token(token: str) -> dict | None:
    """Decode a refresh JWT (see :func:`_refresh_secret` for resolution).

    Returns the payload dict (with ``sub`` and ``jti``) or ``None``. Does
    NOT check ``users.refresh_token_jti`` — that is the route's job.
    """
    secret = _refresh_secret()
    if not secret:
        _log.warning(
            "no refresh secret available — refresh tokens cannot be verified "
            "(set LUMOGIS_JWT_REFRESH_SECRET or JWT_REFRESH_SECRET)"
        )
        return None
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None


# Module-level latch for the multi-user CRITICAL log line so it fires
# exactly once per process — flooding the log on every malformed request
# would drown the signal it's supposed to surface (see plan
# `mcp_token_user_map` §"Modified files / orchestrator/auth.py").
_warned_legacy_fallback_in_multi_user: bool = False


def _warn_legacy_fallback_in_multi_user_once() -> None:
    """Emit a one-shot CRITICAL log when an operator presents legacy
    ``MCP_AUTH_TOKEN`` in multi-user mode.

    Idempotent. The wording points at the per-user mint flow so the
    operator has a clear path forward; ADR ``mcp_token_user_map`` D6 is
    a clean break, not a graceful transition.
    """
    global _warned_legacy_fallback_in_multi_user
    if _warned_legacy_fallback_in_multi_user:
        return
    _warned_legacy_fallback_in_multi_user = True
    _log.critical(
        "multi-user mode is enabled but a /mcp/* request presented the legacy "
        "MCP_AUTH_TOKEN without a per-user JWT or lmcp_… bearer. Per ADR / D6, "
        "this is fail-closed. Mint per-user tokens via "
        "POST /api/v1/me/mcp-tokens (see docs/connect-and-verify.md step 9d)."
    )


def _read_bearer(request: Request) -> str:
    """Extract and trim the ``Authorization: Bearer …`` value (or ``""``)."""
    return request.headers.get("Authorization", "").removeprefix("Bearer ").strip()


def _mcp_401(message: str) -> JSONResponse:
    return JSONResponse(status_code=401, content={"error": message})


def _check_mcp_bearer(request: Request) -> JSONResponse | None:
    """Gate ``/mcp/*`` on the canonical evaluation order from plan
    ``mcp_token_user_map`` §"Modified files / orchestrator/auth.py".

    The branches MUST be evaluated in the order documented below; in
    particular, JWT detection in step 4 MUST run BEFORE the
    ``MCP_AUTH_TOKEN`` compare in step 5 — reordering reintroduces the
    D6 regression where a legacy shared-secret silently rescues a
    multi-user request without any user identity.

    Side effects on hit:

    * ``lmcp_…`` accept: stash ``request.state.mcp_token_id`` and
      ``request.state.mcp_user_id`` so the resolver in
      :mod:`mcp_server` can reuse the verify result without a second
      DB lookup (D8 single-verify cache).
    * ``MCP_AUTH_TOKEN`` legacy match in multi-user mode: emit a CRITICAL
      log once per process via
      :func:`_warn_legacy_fallback_in_multi_user_once`.

    Returns ``None`` to pass through, or a 401 ``JSONResponse`` to reject.
    """
    presented = _read_bearer(request)

    # 1. Empty / missing bearer.
    if not presented:
        if auth_enabled():
            # AUTH_ENABLED=true: never rescue an anonymous /mcp/* request.
            return _mcp_401("missing mcp token")
        # AUTH_ENABLED=false: legacy single-user. Pass through ONLY if
        # the operator hasn't pinned a shared secret. If MCP_AUTH_TOKEN
        # is set, missing-bearer must 401 — the original
        # ``_check_mcp_bearer`` contract. Pinned by
        # ``test_mcp_endpoint_blocks_missing_token_when_token_required``.
        env = os.environ.get("MCP_AUTH_TOKEN", "").strip()
        if env == "":
            return None
        return _mcp_401("invalid mcp token")

    # 2. lmcp_… shape — handled identically in BOTH modes.
    if presented.startswith("lmcp_"):
        # Local import: keep auth.py boot light; services.mcp_tokens
        # touches config + the metadata store. Lazy import also breaks
        # an otherwise-circular dependency between auth and services.
        from services import mcp_tokens as _mcp_tokens

        row = _mcp_tokens.verify(presented)
        if row is None:
            # Explicit user-token miss is ALWAYS a definite reject — never
            # falls back to MCP_AUTH_TOKEN or MCP_DEFAULT_USER_ID, in
            # either mode. Pinned by `test_invalid_lmcp_*_fails_closed_no_rescue`.
            return _mcp_401("invalid mcp token")
        request.state.mcp_token_id = row.id
        request.state.mcp_user_id = row.user_id
        return None

    # 3. AUTH_ENABLED=false — non-lmcp_… bearer.
    if not auth_enabled():
        env = os.environ.get("MCP_AUTH_TOKEN", "").strip()
        if env == "":
            # Legacy "no shared secret set" pass-through. Documented in
            # the original `_check_mcp_bearer` and preserved verbatim.
            return None
        if hmac.compare_digest(presented, env):
            # Legacy single-user accept. Resolver → MCP_DEFAULT_USER_ID.
            return None
        return _mcp_401("invalid mcp token")

    # 4. AUTH_ENABLED=true — JWT detection MUST run BEFORE any
    #    MCP_AUTH_TOKEN compare. Order is load-bearing: D6 forbids
    #    "MCP_AUTH_TOKEN match wins" in multi-user mode. A presented
    #    bearer that decodes as a Lumogis JWT is the ONLY non-lmcp_…
    #    accept path; the resolver later reads sub from _current_bearer.
    if verify_token(presented) is not None:
        return None

    # 5. AUTH_ENABLED=true — non-lmcp_… non-JWT bearer.
    env = os.environ.get("MCP_AUTH_TOKEN", "").strip()
    if env != "" and hmac.compare_digest(presented, env):
        _warn_legacy_fallback_in_multi_user_once()
        return _mcp_401(
            "invalid mcp token (legacy MCP_AUTH_TOKEN not accepted in "
            "multi-user mode; mint a per-user lmcp_… token via "
            "POST /api/v1/me/mcp-tokens)"
        )
    return _mcp_401("invalid mcp token")


# Endpoints exempt from JWT enforcement even when AUTH_ENABLED=true.
# Login is the obvious one. /api/v1/auth/refresh and /logout consume the
# refresh cookie, not a Bearer, so they live outside the bearer gate too —
# the route handlers do their own credential checks. /healthz is plumbing.
# ``/health`` is the detailed status JSON reverse-proxied by Caddy — exempt so
# the front door probe matches legacy smoke behaviour without minting a JWT.
# /web is the static Lumogis Web SPA shell — it must be reachable so the
# user can see the login form (the page body contains no secrets; all
# authenticated work happens in the browser via subsequent fetch calls).
#
# Matching is path-segment-safe (see :func:`_path_is_bypassed`):
# ``/web`` matches ``/web`` and ``/web/anything`` but NOT ``/webhook``.
# Without that guard, naive ``str.startswith`` would carve unintended
# unauthenticated holes whenever any future route happens to share a
# prefix string (``/healthz`` ↔ ``/healthzfoo``, ``/web`` ↔ ``/webhook``).
_AUTH_BYPASS_PREFIXES: tuple[str, ...] = (
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/logout",
    "/health",
    "/healthz",
    "/web",
)


def _path_is_bypassed(path: str) -> bool:
    """Return True iff ``path`` is in the bypass set on a path-segment boundary.

    A bypass entry ``p`` matches when:

    * ``path == p`` (exact), OR
    * ``path`` starts with ``p + "/"`` (subtree).

    Bare ``str.startswith(p)`` would also match siblings whose first
    extra character is NOT a ``/`` (``/web`` would match ``/webhook``).
    Always go through this helper, not ``startswith`` directly.
    """
    for p in _AUTH_BYPASS_PREFIXES:
        if path == p or path.startswith(p + "/"):
            return True
    return False


async def auth_middleware(request: Request, call_next):
    """No-op when ``AUTH_ENABLED=false``. Enforces Bearer JWT when true.

    ``/mcp/*`` is gated independently by ``MCP_AUTH_TOKEN`` regardless of
    ``AUTH_ENABLED``.
    """
    if request.url.path.startswith("/mcp"):
        rejection = _check_mcp_bearer(request)
        if rejection is not None:
            return rejection
        request.state.user = UserContext()
        # Phase 3.1 — make the inbound Bearer visible to MCP tool handlers
        # via the per-request ContextVar in mcp_server. The handler runs
        # inside the same request task (Starlette mounts the FastMCP
        # sub-app and awaits it on the same logical context), so a
        # ContextVar set here is visible to ``_resolve_user_id()``
        # downstream. ``_resolve_user_id`` itself decides whether the
        # token is a Lumogis JWT (use ``sub``) or just the shared
        # ``MCP_AUTH_TOKEN`` (fall back to ``MCP_DEFAULT_USER_ID``).
        presented = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        # Per plan D8 — propagate the verify() result that
        # ``_check_mcp_bearer`` already produced for ``lmcp_…`` tokens via
        # the new per-request ContextVars, so ``_resolve_user_id`` does NOT
        # re-verify against the database. The legacy ``_current_bearer``
        # ContextVar is preserved for the JWT and legacy MCP_AUTH_TOKEN
        # branches, which still need string-level access to the bearer.
        from mcp_server import (
            _reset_current_bearer,
            _reset_current_mcp_token_id,
            _reset_current_mcp_user_id,
            _set_current_bearer,
            _set_current_mcp_token_id,
            _set_current_mcp_user_id,
        )

        bearer_reset = _set_current_bearer(presented or None)
        token_id_reset = _set_current_mcp_token_id(
            getattr(request.state, "mcp_token_id", None)
        )
        user_id_reset = _set_current_mcp_user_id(
            getattr(request.state, "mcp_user_id", None)
        )
        try:
            return await call_next(request)
        finally:
            _reset_current_mcp_user_id(user_id_reset)
            _reset_current_mcp_token_id(token_id_reset)
            _reset_current_bearer(bearer_reset)

    if not auth_enabled():
        request.state.user = UserContext()
        return await call_next(request)

    # AUTH_ENABLED=true from here on.
    if _path_is_bypassed(request.url.path):
        # No user attached for the bypass paths — handlers know to read
        # credentials from the request directly.
        return await call_next(request)

    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token:
        return JSONResponse(status_code=401, content={"error": "missing token"})

    payload = verify_token(token)
    if not payload:
        return JSONResponse(status_code=401, content={"error": "invalid token"})

    role = payload.get("role", "user")
    if role not in ("admin", "user"):
        # Strict typing — never silently demote an unknown role to "user".
        return JSONResponse(status_code=401, content={"error": "invalid role"})

    request.state.user = UserContext(
        user_id=payload["sub"],
        is_authenticated=True,
        role=role,
    )
    return await call_next(request)


def get_user(request: Request) -> UserContext:
    """Convenience: extracts user context set by ``auth_middleware``."""
    return getattr(request.state, "user", UserContext())
