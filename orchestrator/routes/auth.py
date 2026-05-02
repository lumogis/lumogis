# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Browser-facing auth endpoints.

Mounted at ``/api/v1/auth/*``. Single responsibility: authenticate the
caller, mint and rotate JWTs, expose the current user identity. All
admin user-management lives in ``routes/admin.py`` (Phase 2).

Endpoint summary
----------------
* ``POST /api/v1/auth/login``   — verify (email, password); return access
  JWT and (when configured) set the refresh cookie.
* ``POST /api/v1/auth/refresh`` — rotate the refresh JWT and issue a new
  access token. Single-active-jti per user enforced via
  ``users.refresh_token_jti``.
* ``POST /api/v1/auth/logout``  — clear the refresh jti server-side and
  expire the cookie.
* ``GET  /api/v1/auth/me``      — return the calling user's
  :class:`UserPublic` snapshot (or the synthesised dev user when
  ``AUTH_ENABLED=false``).

Rate limiting
-------------
In-process token-bucket. **5 failed logins / IP / 60 s** AND **5 failed
logins / email / 60 s**. Single-uvicorn-worker assumption documented in
``orchestrator/Dockerfile`` (no ``--workers`` flag). If a future change
moves to multi-worker the limiter must move to Postgres or Redis (flagged
in §19 of the family-LAN plan).
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections import defaultdict
from collections import deque
from threading import Lock
from typing import Deque

import services.users as users_svc
from auth import UserContext
from auth import access_token_ttl_seconds
from auth import auth_enabled
from auth import get_user
from auth import mint_access_token
from auth import mint_refresh_token
from auth import refresh_token_ttl_seconds
from auth import verify_refresh_token
from csrf import _proxied_client_ip
from csrf import require_same_origin
from fastapi import APIRouter
from fastapi import Cookie
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi import status
from models.auth import LoginRequest
from models.auth import LoginResponse
from models.auth import UserPublic

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

REFRESH_COOKIE_NAME = "lumogis_refresh"
REFRESH_COOKIE_PATH = "/api/v1/auth"


# ---------------------------------------------------------------------------
# Rate limiter (per-IP and per-email, in-process token bucket)
# ---------------------------------------------------------------------------

_RATE_WINDOW_SECONDS = 60.0
_RATE_MAX_FAILURES = 5

_rate_lock = Lock()
_rate_ip: dict[str, Deque[float]] = defaultdict(deque)
_rate_email: dict[str, Deque[float]] = defaultdict(deque)


def _rate_check(key_ip: str, key_email: str) -> bool:
    """Return ``True`` if the request is within the window for both keys."""
    now = time.monotonic()
    cutoff = now - _RATE_WINDOW_SECONDS
    with _rate_lock:
        for store in (_rate_ip[key_ip], _rate_email[key_email]):
            while store and store[0] < cutoff:
                store.popleft()
            if len(store) >= _RATE_MAX_FAILURES:
                return False
        return True


def _rate_record_failure(key_ip: str, key_email: str) -> None:
    now = time.monotonic()
    with _rate_lock:
        _rate_ip[key_ip].append(now)
        _rate_email[key_email].append(now)


def _rate_record_success(key_ip: str, key_email: str) -> None:
    """Reset the per-email bucket on success; per-IP keeps its history."""
    with _rate_lock:
        _rate_email[key_email].clear()


def _reset_rate_limit_for_tests() -> None:
    """Test helper — wipe the in-process counters between cases."""
    with _rate_lock:
        _rate_ip.clear()
        _rate_email.clear()


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------


def _cookie_secure() -> bool:
    """Default Secure=True; allow opt-out for HTTP dev via env."""
    return os.environ.get("LUMOGIS_REFRESH_COOKIE_SECURE", "true").strip().lower() == "true"


def _set_refresh_cookie(response: Response, refresh_jwt: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_jwt,
        max_age=refresh_token_ttl_seconds(),
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=_cookie_secure(),
        samesite="strict",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value="",
        max_age=0,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=_cookie_secure(),
        samesite="strict",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_ip(request: Request) -> str:
    """Resolve the rate-limiter key IP, honouring X-Forwarded-For when
    the immediate peer is on the ``LUMOGIS_TRUSTED_PROXIES`` allowlist.

    Delegates to :func:`csrf._proxied_client_ip`. See that module for
    the trusted-proxy resolution rules. This fix is the cross-device
    plan's D5 "rate-limit collapse behind reverse proxy" gap — without
    it every request from a Caddy / nginx front door collapses onto
    the proxy's own IP and the per-IP failed-login bucket becomes
    deployment-wide instead of per-client.
    """
    return _proxied_client_ip(request)


def _login_response(user_id: str, role: str, email: str) -> LoginResponse:
    return LoginResponse(
        access_token=mint_access_token(user_id, role),
        token_type="bearer",
        expires_in=access_token_ttl_seconds(),
        user=UserPublic(id=user_id, email=email, role=role),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, response: Response) -> LoginResponse:
    """Verify credentials. Returns an access JWT and rotates the refresh cookie.

    Status codes:

    * ``200`` — success.
    * ``401`` — bad credentials, unknown email, or disabled user. The
      same body and latency floor apply to all three to defeat enumeration.
    * ``429`` — rate-limit exceeded (per-IP or per-email).
    * ``503`` — ``AUTH_ENABLED=false`` (login is meaningless in dev mode).
    """
    if not auth_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="login is disabled in single-user dev mode",
        )

    ip = _client_ip(request)
    email_key = body.email.strip().lower()

    if not _rate_check(ip, email_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many failed attempts; try again in a minute",
            headers={"Retry-After": "60"},
        )

    user = users_svc.verify_credentials(body.email, body.password)
    if user is None:
        _rate_record_failure(ip, email_key)
        # Generic 401 — no enumeration via response shape or status code.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )

    _rate_record_success(ip, email_key)

    new_jti = uuid.uuid4().hex
    users_svc.set_refresh_jti(user.id, new_jti)
    users_svc.record_login(user.id)

    refresh_jwt = mint_refresh_token(user.id, new_jti)
    _set_refresh_cookie(response, refresh_jwt)

    return _login_response(user.id, user.role, user.email)


@router.post(
    "/refresh",
    response_model=LoginResponse,
    dependencies=[Depends(require_same_origin)],
)
def refresh(
    request: Request,
    response: Response,
    lumogis_refresh: str | None = Cookie(default=None),
) -> LoginResponse:
    """Rotate the refresh JWT and issue a new access token.

    Failure modes (see plan §12):

    * Cookie absent / signature invalid / expired / format invalid → 401.
    * ``jti != users.refresh_token_jti`` → 401, defensively clear the
      column, clear the cookie. Possible token-theft signal — logged
      ``WARNING`` with ``user_id``.
    * User row missing or ``disabled = TRUE`` → 401, clear cookie.
    * DB error during rotation → 500, do not issue new tokens; old cookie
      remains valid for retry.
    """
    if not auth_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="refresh is disabled in single-user dev mode",
        )

    if not lumogis_refresh:
        raise HTTPException(status_code=401, detail="missing refresh cookie")

    payload = verify_refresh_token(lumogis_refresh)
    if not payload or "sub" not in payload or "jti" not in payload:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="invalid refresh token")

    user_id = payload["sub"]
    presented_jti = payload["jti"]

    user = users_svc.get_user_by_id(user_id)
    if user is None or user.disabled:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="invalid refresh token")

    active_jti = users_svc.get_refresh_jti(user_id)
    if active_jti is None or active_jti != presented_jti:
        users_svc.set_refresh_jti(user_id, None)
        _clear_refresh_cookie(response)
        _log.warning(
            "refresh: jti mismatch user_id=%s (possible replay or evicted by newer login)",
            user_id,
        )
        raise HTTPException(status_code=401, detail="invalid refresh token")

    new_jti = uuid.uuid4().hex
    try:
        users_svc.set_refresh_jti(user_id, new_jti)
    except Exception as exc:
        _log.exception("refresh: rotation update failed for user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="refresh rotation failed") from exc

    new_refresh = mint_refresh_token(user_id, new_jti)
    _set_refresh_cookie(response, new_refresh)
    return _login_response(user.id, user.role, user.email)


@router.post("/logout")
def logout(
    response: Response,
    lumogis_refresh: str | None = Cookie(default=None),
) -> dict:
    """Clear the refresh cookie and the server-side jti.

    Idempotent and tolerant of missing/invalid cookies — a logout request
    that arrives without a valid cookie still clears any stale state.
    """
    if lumogis_refresh:
        payload = verify_refresh_token(lumogis_refresh)
        if payload and "sub" in payload:
            try:
                users_svc.set_refresh_jti(payload["sub"], None)
            except Exception:
                _log.exception("logout: failed to clear refresh_token_jti")
    _clear_refresh_cookie(response)
    return {"ok": True}


@router.get("/me", response_model=UserPublic)
def me(request: Request) -> UserPublic:
    """Return the current user's :class:`UserPublic` snapshot.

    In dev mode (``AUTH_ENABLED=false``) returns the synthesised default
    admin so the dashboard widget can render ``Single-user mode (admin)``
    without a login flow.
    """
    ctx: UserContext = get_user(request)
    if not auth_enabled():
        return UserPublic(id=ctx.user_id, email="dev@local.lan", role="admin")
    if not ctx.is_authenticated:
        raise HTTPException(status_code=401, detail="not authenticated")
    user = users_svc.get_user_by_id(ctx.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    return UserPublic(id=user.id, email=user.email, role=user.role)
