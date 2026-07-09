# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Public household invite peek/redeem routes (LUM-186)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from auth import auth_enabled
from auth import mint_refresh_token
from auth import refresh_token_ttl_seconds
from csrf import _proxied_client_ip
from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi import status
from models.user_invite import DuplicateEmailError
from models.user_invite import EmailPolicyViolationError
from models.user_invite import InviteInvalidError
from models.user_invite import InviteOnboardingHint
from models.user_invite import InvitePeekPublic
from models.user_invite import InviteRedeemRequest
from models.user_invite import InviteRedeemResponse
from rate_limit import FailureRateLimiter
from rate_limit import RequestRateLimiter
from routes.auth import _login_response
from routes.auth import _set_refresh_cookie
from services import auth_sessions as auth_sess
from services import user_invites as invites_service
from services import users as users_svc
from services.users import PasswordPolicyViolationError

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/invites", tags=["invites"])

_peek_limiter = RequestRateLimiter(max_requests=30, window_seconds=60.0)
_redeem_limiter = FailureRateLimiter()

_INVITE_INVALID_DETAIL = "Invite link is invalid or expired"
_DUPLICATE_EMAIL_DETAIL = "An account with this email already exists"


def _client_ip(request: Request) -> str:
    return _proxied_client_ip(request)


def _reset_limiters_for_tests() -> None:
    _peek_limiter.clear()
    _redeem_limiter.clear()


@router.get("/{token}")
def peek_invite(token: str, request: Request) -> InvitePeekPublic:
    if not auth_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="invites are disabled in single-user dev mode",
        )
    ip = _client_ip(request)
    if not _peek_limiter.check(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many requests; try again in a minute",
            headers={"Retry-After": "60"},
        )
    meta = invites_service.peek_invite(token)
    if meta is None:
        raise HTTPException(status_code=404, detail=_INVITE_INVALID_DETAIL)
    return meta


@router.post("/{token}/redeem", response_model=InviteRedeemResponse)
def redeem_invite(
    token: str,
    body: InviteRedeemRequest,
    request: Request,
    response: Response,
) -> InviteRedeemResponse:
    if not auth_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="invites are disabled in single-user dev mode",
        )

    ip = _client_ip(request)
    email_key = str(body.email).strip().lower()
    if not _redeem_limiter.check(ip, email_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many failed attempts; try again in a minute",
            headers={"Retry-After": "60"},
        )

    try:
        result = invites_service.redeem_invite(token, str(body.email), body.password)
    except DuplicateEmailError:
        _redeem_limiter.record_failure(ip, email_key)
        raise HTTPException(status_code=409, detail=_DUPLICATE_EMAIL_DETAIL) from None
    except PasswordPolicyViolationError as exc:
        _redeem_limiter.record_failure(ip, email_key)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except EmailPolicyViolationError as exc:
        _redeem_limiter.record_failure(ip, email_key)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except InviteInvalidError:
        _redeem_limiter.record_failure(ip, email_key)
        raise HTTPException(status_code=404, detail=_INVITE_INVALID_DETAIL) from None

    _redeem_limiter.record_success(ip, email_key)

    user = result.user
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

    login = _login_response(
        user.id,
        user.role,
        user.email,
        session_id=session_id,
        token_version=user.token_version,
        allows_shared=user.allows_shared,
    )
    return InviteRedeemResponse(
        access_token=login.access_token,
        token_type=login.token_type,
        expires_in=login.expires_in,
        user=login.user,
        invite_onboarding=InviteOnboardingHint(allows_shared=result.allows_shared),
    )
