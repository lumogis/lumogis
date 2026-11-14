# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Admin → System mail: the only writer of ``system_smtp`` (LUM-65).

Every generic credential router refuses this connector (422
``use_system_mail_admin``), so configuration has one path in. That is what
makes it safe to keep ``last_test_ok_at`` inside the encrypted payload: a
single writer cannot race itself into a state where the stamp outlives the
configuration it vouched for.

Enablement is deliberately hard to fake. ``email_reset_enabled`` requires a
Send test that actually delivered — not "SMTP looks configured" — so the
public forgot-password form never appears for a relay that has never sent
anything.
"""

from __future__ import annotations

import logging

from auth import get_user
from authz import require_admin
from csrf import require_same_origin
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from models.auth import AckOk
from models.password_reset import SystemMailPublic
from models.password_reset import SystemMailPutBody
from models.password_reset import SystemMailTestBody
from models.password_reset import SystemMailTestResponse
from services.outbound_http_url import validate_outbound_host

from services import system_mail
from services import users as users_svc

_log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/system-mail",
    tags=["admin-system-mail"],
    dependencies=[Depends(require_admin)],
)

# Changing any of these invalidates the proof that the relay delivers.
_MATERIAL_FIELDS = ("host", "port", "username", "from_addr")


def _current_public() -> SystemMailPublic:
    payload = system_mail.load_raw_payload() or {}
    cfg = system_mail.load_config()
    return SystemMailPublic(
        configured=bool(payload),
        host=payload.get("host"),
        port=payload.get("port"),
        username=payload.get("username"),
        from_addr=payload.get("from_addr"),
        password_set=bool(payload.get("password")),
        last_test_ok_at=cfg.last_test_ok_at if cfg else None,
        email_reset_enabled=system_mail.email_reset_enabled(),
        public_origin_set=system_mail.public_origin_set(),
        altcha_hmac_set=system_mail.altcha_hmac_set(),
    )


@router.get("", response_model=SystemMailPublic)
def get_system_mail() -> SystemMailPublic:
    """Redacted configuration plus the individual enablement preconditions.

    The password is never returned in any form — ``password_set`` is the only
    thing the UI needs in order to render "leave blank to keep".
    """
    return _current_public()


@router.put(
    "",
    response_model=SystemMailPublic,
    dependencies=[Depends(require_same_origin)],
)
def put_system_mail(body: SystemMailPutBody, request: Request) -> SystemMailPublic:
    """Create or update the system mailer.

    Password semantics: omit the field to keep the stored one; an explicit
    ``null``/``""`` is a 422 rather than a silent clear, so a UI that
    round-trips its own redacted GET cannot blank the credential by accident.
    DELETE is how you actually remove it.
    """
    if body.password is not None and not body.password.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "password_cannot_be_cleared",
                "message": "omit `password` to keep the current one, or DELETE to remove it",
            },
        )

    # SSRF gate before anything is stored. Reuses the connector host policy
    # (`services.outbound_http_url`) rather than a second copy: a LAN or
    # loopback MTA is legitimate, cloud metadata never is.
    try:
        validate_outbound_host(body.host)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "host_not_allowed", "message": str(exc)},
        ) from exc

    existing = system_mail.load_raw_payload() or {}
    password = body.password if body.password is not None else existing.get("password")
    if not password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "password_required", "message": "no stored password to keep"},
        )

    payload = {
        "host": body.host.strip(),
        "port": int(body.port),
        "username": body.username.strip(),
        "password": password,
        "from_addr": str(body.from_addr).strip(),
    }

    # Any material change unproves the relay: the Send test that set the stamp
    # validated a configuration that no longer exists. Carrying the stamp over
    # would leave email reset enabled for a host nobody has ever delivered to.
    changed = any(str(existing.get(f, "")) != str(payload[f]) for f in _MATERIAL_FIELDS)
    password_changed = existing.get("password") != password
    if not (changed or password_changed) and existing.get("last_test_ok_at"):
        payload["last_test_ok_at"] = existing["last_test_ok_at"]

    caller = get_user(request)
    system_mail.save_payload(payload, actor=f"admin:{caller.user_id}")
    _log.info("system mail configured by admin=%s host=%s", caller.user_id, payload["host"])
    return _current_public()


@router.delete(
    "",
    response_model=AckOk,
    dependencies=[Depends(require_same_origin)],
)
def delete_system_mail(request: Request) -> AckOk:
    caller = get_user(request)
    if not system_mail.delete_config(actor=f"admin:{caller.user_id}"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "connector_not_configured"},
        )
    return AckOk()


@router.post(
    "/test",
    response_model=SystemMailTestResponse,
    dependencies=[Depends(require_same_origin)],
)
def test_system_mail(body: SystemMailTestBody, request: Request) -> SystemMailTestResponse:
    """Send a probe and, only on success, stamp ``last_test_ok_at``.

    Synchronous — unlike reset mail, an admin is sitting here waiting for the
    answer, and the answer is the entire point of the button.
    """
    caller = get_user(request)

    # Fail closed on the other two preconditions too, so an operator cannot
    # "prove" mail works and still be left with a disabled feature and no clue
    # which conjunct is missing.
    if not system_mail.public_origin_set():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "public_origin_required",
                "message": "set LUMOGIS_PUBLIC_ORIGIN — reset links are built from it",
            },
        )
    if not system_mail.altcha_hmac_set():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "altcha_hmac_required",
                "message": "set LUMOGIS_ALTCHA_HMAC_KEY — the public request path needs it",
            },
        )

    to_addr = str(body.to) if body.to else None
    if not to_addr:
        me = users_svc.get_user_by_id(caller.user_id)
        to_addr = me.email if me else None
    if not to_addr:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "recipient_required"},
        )

    try:
        stamped = system_mail.send_test(to_addr=to_addr, actor=f"admin:{caller.user_id}")
    except system_mail.SystemMailNotConfigured:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "system_mail_not_configured"},
        ) from None
    except Exception as exc:  # noqa: BLE001 - adapter collapses every failure here
        _log.warning("system mail test failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "send_failed", "message": str(exc)},
        ) from exc

    return SystemMailTestResponse(ok=True, last_test_ok_at=stamped)
