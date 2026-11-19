# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""First-admin setup routes (LUM-981).

``GET  /api/v1/setup/status`` — ``{"needs_setup": bool}``, from a process flag
only (no database), so Lumogis Web can ask on every boot.

``POST /api/v1/setup/claim`` — the first visitor creates the household admin
and is signed in. Public (``/api/v1/setup`` is an auth bypass prefix), so it
guards itself, in this order: the ``Host`` allow-list (DNS rebinding), the
same-origin check, then the body. A failed policy check counts against a
per-IP and per-email limiter. One admin can ever be created: see
``services/instance_setup`` for the race-safe transaction.

Both handlers are sync ``def``: they block on the database and argon2, so
FastAPI runs them in its threadpool.
"""

from __future__ import annotations

import logging

import psycopg2
import services.instance_setup as instance_setup
from auth import auth_enabled
from csrf import _proxied_client_ip
from csrf import claim_host_allowed
from csrf import require_same_origin
from fastapi import APIRouter
from fastapi import BackgroundTasks
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi import status
from models.auth import LoginResponse
from models.setup import SetupClaimRequest
from models.setup import SetupStatus
from ports.metadata_store import PoolExhaustedError
from rate_limit import FailureRateLimiter
from routes.auth import _start_session
from services.users import PasswordPolicyViolationError

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/setup", tags=["setup"])

_claim_limiter = FailureRateLimiter()

_NOT_OPEN_DETAIL = "setup is not open"


def _reset_limiters_for_tests() -> None:
    _claim_limiter.clear()


def _require_claim_host(request: Request) -> None:
    """First dependency of the claim: refuse a ``Host`` an attacker page could rebind to."""
    if not claim_host_allowed(request):
        shown = request.headers.get("Host", "")[:100]
        _log.warning("setup claim refused: host not allowed (Host=%r)", shown)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="setup_host_not_allowed")


def _remap_after_claim() -> None:
    """Move ``user_id='default'`` rows to the new admin now, not at the next boot."""
    import db_default_user_remap

    db_default_user_remap.run_post_bootstrap_safely("after setup claim")


@router.get("/status", response_model=SetupStatus)
def setup_status(response: Response) -> SetupStatus:
    """``needs_setup`` is true only while the first admin can still be created."""
    response.headers["Cache-Control"] = "no-store"
    return SetupStatus(needs_setup=auth_enabled() and instance_setup.is_open())


@router.post(
    "/claim",
    response_model=LoginResponse,
    dependencies=[Depends(_require_claim_host), Depends(require_same_origin)],
)
def claim_setup(
    body: SetupClaimRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
) -> LoginResponse:
    """Create the first admin and sign them in.

    * ``200`` — a ``LoginResponse`` and the ``lumogis_refresh`` cookie.
    * ``409`` — setup is not open (sign-in off, already claimed, or lost a race).
    * ``403`` — ``setup_host_not_allowed`` or ``origin mismatch``.
    * ``422`` — email or password policy.
    * ``429`` — too many failed attempts.
    * ``503`` — ``database_unavailable``.
    """
    if not auth_enabled() or not instance_setup.is_open():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_NOT_OPEN_DETAIL)

    ip = _proxied_client_ip(request)
    email_key = str(body.email).strip().lower()
    if not _claim_limiter.check(ip, email_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many failed attempts; try again in a minute",
            headers={"Retry-After": "60"},
        )

    # Only these two validators map to 422 here. A ValueError raised inside the
    # claim transaction is not an input error and must not become one.
    try:
        instance_setup.validate_claim(str(body.email), body.password)
    except (ValueError, PasswordPolicyViolationError) as exc:
        _claim_limiter.record_failure(ip, email_key)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        user = instance_setup.claim(str(body.email), body.password)
    except instance_setup.SetupNotOpen:
        _log.info("setup claim refused: not open")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_NOT_OPEN_DETAIL) from None
    except (psycopg2.Error, PoolExhaustedError):
        _log.exception("setup claim failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="database_unavailable"
        ) from None

    _claim_limiter.record_success(ip, email_key)
    try:
        instance_setup.remove_reminder_job()
    except Exception:  # noqa: BLE001 — the reminder is cosmetic; the claim succeeded
        _log.debug("setup reminder job removal failed", exc_info=True)
    background_tasks.add_task(_remap_after_claim)

    return _start_session(user, request, response)
