# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Secure address at home — admin and member routes (LUM-331).

``GET`` reads cached state only (no network); every write takes the
secure-address lock for at most 2 s (``409 busy`` otherwise) and does its
network work in a background task, so a slow Worker never holds a request.
``POST /detect`` runs the host-address helper on demand (up to ~50 s on a slow
Docker daemon, or up to ~55 s waiting for a detection already running; typically
1-3 s); ``GET`` stays read-only.
"""

from __future__ import annotations

from auth import UserContext
from auth import auth_enabled
from authz import require_admin
from authz import require_user
from csrf import require_same_origin
from fastapi import APIRouter
from fastapi import BackgroundTasks
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi import status
from models.api_v1 import SecureAddressEnableRequest
from models.api_v1 import SecureAddressLanAddressRequest
from models.api_v1 import SecureAddressPublic
from models.api_v1 import SecureAddressStatus
from services.home_dns import service
from services.home_dns import state as st
from services.home_dns import validate

router = APIRouter(
    prefix="/api/v1/admin/secure-address",
    tags=["admin-secure-address"],
)
public_router = APIRouter(prefix="/api/v1/secure-address", tags=["secure-address"])

_NO_STORE = {"Cache-Control": "no-store"}


def _status(request: Request) -> SecureAddressStatus:
    return service.status(request.headers.get("Host"), sign_in_enabled=auth_enabled())


def _require_lan_address(value: str) -> str:
    if not validate.valid_lan_ipv4(value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="lan_address must be a home-network IPv4 address (10.x, 172.16-31.x, 192.168.x)",
        )
    return value


def _busy() -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="busy")


@router.get("", response_model=SecureAddressStatus)
def get_secure_address(
    request: Request, response: Response, _user: UserContext = Depends(require_admin)
) -> SecureAddressStatus:
    response.headers.update(_NO_STORE)
    return _status(request)


@router.post(
    "/enable",
    response_model=SecureAddressStatus,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_same_origin)],
)
def enable_secure_address(
    body: SecureAddressEnableRequest,
    request: Request,
    background: BackgroundTasks,
    user: UserContext = Depends(require_admin),
) -> SecureAddressStatus:
    address = _require_lan_address(body.lan_address)
    try:
        with service.route_lock():
            if st.load().enabled:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="already on")
            generation = service.enable(address, request.headers.get("Host"))
    except service.Busy:
        raise _busy() from None
    service.audit("secure_address.enable", user.user_id, {"lan_address": address})
    background.add_task(service._advance, generation)
    background.add_task(service.ensure_fast_job)
    return _status(request)


@router.put(
    "/lan-address",
    response_model=SecureAddressStatus,
    dependencies=[Depends(require_same_origin)],
)
def put_lan_address(
    body: SecureAddressLanAddressRequest,
    request: Request,
    background: BackgroundTasks,
    user: UserContext = Depends(require_admin),
) -> SecureAddressStatus:
    address = _require_lan_address(body.lan_address)
    try:
        with service.route_lock():
            if not st.load().enabled:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="off")
            generation = service.set_lan_address(address, request.headers.get("Host"))
    except service.Busy:
        raise _busy() from None
    service.audit("secure_address.lan_address", user.user_id, {"lan_address": address})
    background.add_task(service._advance, generation)
    return _status(request)


@router.post(
    "/retry",
    response_model=SecureAddressStatus,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_same_origin)],
)
def retry_secure_address(
    request: Request,
    background: BackgroundTasks,
    _user: UserContext = Depends(require_admin),
) -> SecureAddressStatus:
    try:
        with service.route_lock():
            if not st.load().enabled:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="off")
            generation = service.retry()
    except service.Busy:
        raise _busy() from None
    background.add_task(service._advance, generation)
    # _advance does nothing once the site is applied, so a Retry after a refused
    # re-apply needs the full run, and it must wait for a job that is running.
    background.add_task(service.refresh_when_free)
    background.add_task(service.ensure_fast_job)
    return _status(request)


@router.post(
    "/detect",
    response_model=SecureAddressStatus,
    dependencies=[Depends(require_same_origin)],
)
def detect_secure_address(
    request: Request,
    response: Response,
    _user: UserContext = Depends(require_admin),
) -> SecureAddressStatus:
    """Look for this computer's home network address now and record the result.

    Changes no configuration (no audit row); while the feature is on, the next
    refresh applies the usual rules for a detected address.
    """
    try:
        service.detect_now()
    except service.Busy:
        raise _busy() from None
    response.headers.update(_NO_STORE)
    return _status(request)


@router.post(
    "/disable",
    response_model=SecureAddressStatus,
    dependencies=[Depends(require_same_origin)],
)
def disable_secure_address(
    request: Request,
    background: BackgroundTasks,
    user: UserContext = Depends(require_admin),
) -> SecureAddressStatus:
    try:
        with service.route_lock():
            service.disable()
    except service.Busy:
        raise _busy() from None
    service.audit("secure_address.disable", user.user_id, {})
    background.add_task(service.after_disable)
    return _status(request)


@public_router.get("", response_model=SecureAddressPublic)
def get_public_secure_address(
    response: Response, _user: UserContext = Depends(require_user)
) -> SecureAddressPublic:
    response.headers.update(_NO_STORE)
    return service.public_view()
