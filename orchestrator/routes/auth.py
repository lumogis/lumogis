# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Browser-facing auth endpoints.

Mounted at ``/api/v1/auth/*``. Single responsibility: authenticate the
caller, mint and rotate JWTs, expose the current user identity.

Session contract (LUM-29): refresh bearer ``jti`` maps to ``auth_sessions.id``;
multi-device rows track ``family_id`` for reuse detection.

Endpoint summary
----------------
* ``POST /api/v1/auth/login``   — verify (email, password); return access
  JWT and (when configured) set the refresh cookie.
* ``POST /api/v1/auth/refresh`` — rotate refresh via ``auth_sessions`` and
  issue a new access token.
* ``POST /api/v1/auth/logout``  — revoke the current ``auth_sessions`` row
  and expire the cookie.
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
import uuid
from datetime import datetime
from datetime import timedelta
from datetime import timezone

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
from rate_limit import FailureRateLimiter
from services.auth_sessions import RefreshError
from services.auth_sessions import RefreshReuseError

from services import auth_sessions as auth_sess

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

REFRESH_COOKIE_NAME = "lumogis_refresh"
REFRESH_COOKIE_PATH = "/api/v1/auth"


# ---------------------------------------------------------------------------
# Rate limiter (per-IP and per-email, in-process token bucket)
# ---------------------------------------------------------------------------

_login_limiter = FailureRateLimiter()


def _rate_check(key_ip: str, key_email: str) -> bool:
    return _login_limiter.check(key_ip, key_email)


def _rate_record_failure(key_ip: str, key_email: str) -> None:
    _login_limiter.record_failure(key_ip, key_email)


def _rate_record_success(key_ip: str, key_email: str) -> None:
    _login_limiter.record_success(key_ip, key_email)


def _reset_rate_limit_for_tests() -> None:
    """Test helper — wipe the in-process counters between cases."""
    _login_limiter.clear()


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


def _login_response(
    user_id: str,
    role: str,
    email: str,
    *,
    session_id: str,
    token_version: int,
    allows_shared: bool = True,
) -> LoginResponse:
    return LoginResponse(
        access_token=mint_access_token(
            user_id,
            role,
            session_id=session_id,
            token_version=token_version,
            allows_shared=allows_shared,
        ),
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

    salt = auth_sess.ensure_instance_salt()
    session_id = uuid.uuid4().hex
    refresh_jwt = mint_refresh_token(user.id, session_id)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=refresh_token_ttl_seconds())
    ua = request.headers.get("User-Agent") or ""
    ip_h, ua_h = auth_sess.fingerprint_hashes(
        normalized_ip=_client_ip(request),
        user_agent=ua,
        salt=salt,
    )
    label = auth_sess.compute_device_label(ua, utc_now=datetime.now(timezone.utc))
    auth_sess.insert_login_session(
        session_id=session_id,
        user_id=user.id,
        refresh_jwt=refresh_jwt,
        expires_at=expires_at,
        device_label=label,
        ip_hash=ip_h,
        ua_hash=ua_h,
    )
    users_svc.record_login(user.id)

    _set_refresh_cookie(response, refresh_jwt)

    return _login_response(
        user.id,
        user.role,
        user.email,
        session_id=session_id,
        token_version=user.token_version,
        allows_shared=user.allows_shared,
    )


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
    """Rotate the refresh JWT via ``auth_sessions`` and issue a new access token."""
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

    user_id = str(payload["sub"])
    presented_jti = str(payload["jti"])

    user = users_svc.get_user_by_id(user_id)
    if user is None or user.disabled:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="invalid refresh token")

    try:
        new_sid, new_refresh, tv = auth_sess.rotate_refresh(
            user_id=user_id,
            jti=presented_jti,
            refresh_jwt=lumogis_refresh,
            refresh_ttl_seconds=refresh_token_ttl_seconds(),
        )
    except RefreshReuseError:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="invalid refresh token") from None
    except RefreshError:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="invalid refresh token") from None
    except Exception as exc:
        _log.exception("refresh: rotation failed for user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="refresh rotation failed") from exc

    user2 = users_svc.get_user_by_id(user_id)
    if user2 is None:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="invalid refresh token")

    _set_refresh_cookie(response, new_refresh)
    return _login_response(
        user2.id,
        user2.role,
        user2.email,
        session_id=new_sid,
        token_version=int(tv),
        allows_shared=user2.allows_shared,
    )


@router.post("/logout")
def logout(
    response: Response,
    lumogis_refresh: str | None = Cookie(default=None),
) -> dict:
    """Revoke the refresh session server-side and expire the cookie."""

    if lumogis_refresh:
        payload = verify_refresh_token(lumogis_refresh)
        if payload and "sub" in payload and "jti" in payload:
            try:
                auth_sess.revoke_session_for_user(
                    session_id=str(payload["jti"]),
                    user_id=str(payload["sub"]),
                )
            except Exception:
                _log.exception("logout: failed to revoke auth session row")
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
