# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Web Push subscription endpoints for the v1 façade.

Phase 0 ships the routes + DB schema (`webpush_subscriptions`,
migration 019). Phase 4A wires outbound delivery in
:mod:`services.webpush` (:func:`send_dev_echo_push_for_user` for the dev
``GET /test`` route). The
``/test`` route is gated on ``WEBPUSH_DEV_ECHO=true`` so dev
machines can dry-run pushes without widening production surface area.

Phase **4B** adds ``GET/PATCH …/notifications/subscriptions`` with
credential-safe DTOs (endpoint origin only).

Idempotency contract (plan §API routes → Notifications):

* First subscribe → ``201 {"id": int, "already_existed": false}``
* Re-subscribe with same ``(user_id, endpoint)`` →
  ``200 {"id": <existing>, "already_existed": true}`` and
  ``last_seen_at`` is bumped.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

from auth import get_user
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi import status
from models.api_v1 import VapidPublicKeyResponse
from models.api_v1 import WebPushSubscriptionCreated
from models.api_v1 import WebPushSubscriptionInput
from models.api_v1 import WebPushSubscriptionPrefsPatch
from models.api_v1 import WebPushSubscriptionRedacted
from models.api_v1 import WebPushSubscriptionsListResponse

import config
from services import webpush as webpush_svc

_log = logging.getLogger(__name__)

_MAX_UA_CHARS = 256
_MAX_ERR_CHARS = 512

router = APIRouter(
    prefix="/api/v1/notifications",
    tags=["v1-notifications"],
    dependencies=[Depends(require_user)],
)


def _redact_endpoint_origin(endpoint: str) -> str:
    """Return scheme + netloc (host[:port]) — strip userinfo; omit path/query."""
    if not endpoint or not str(endpoint).strip():
        return "unknown"
    raw = str(endpoint).strip()
    try:
        p = urlparse(raw if "://" in raw else f"https://{raw}")
        hostpart = (p.netloc or "").strip()
        if not hostpart:
            return "unknown"
        authority = hostpart.split("@")[-1].strip()
        if not authority:
            return "unknown"
        scheme = (p.scheme or "https").lower()
        return f"{scheme}://{authority}"
    except Exception:
        return "unknown"


def _truncate_optional(s: str | None, max_len: int) -> str | None:
    if s is None:
        return None
    t = s.strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 3] + "..."


def _row_to_redacted(row: dict) -> WebPushSubscriptionRedacted:
    return WebPushSubscriptionRedacted(
        id=int(row["id"]),
        endpoint_origin=_redact_endpoint_origin(row.get("endpoint") or ""),
        created_at=row["created_at"],
        last_seen_at=row["last_seen_at"],
        last_error=_truncate_optional(row.get("last_error"), _MAX_ERR_CHARS),
        user_agent=_truncate_optional(row.get("user_agent"), _MAX_UA_CHARS),
        notify_on_signals=bool(row["notify_on_signals"]),
        notify_on_shared_scope=bool(row["notify_on_shared_scope"]),
    )


def _vapid_public_key() -> str | None:
    return os.environ.get("WEBPUSH_VAPID_PUBLIC_KEY") or None


def _webpush_configured() -> bool:
    return bool(
        os.environ.get("WEBPUSH_VAPID_PUBLIC_KEY") and os.environ.get("WEBPUSH_VAPID_PRIVATE_KEY")
    )


def _require_webpush_configured() -> None:
    if not _webpush_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "webpush_not_configured"},
        )


@router.get("/vapid-public-key", response_model=VapidPublicKeyResponse)
def vapid_public_key() -> VapidPublicKeyResponse:
    key = _vapid_public_key()
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "webpush_not_configured"},
        )
    return VapidPublicKeyResponse(public_key=key)


@router.get("/subscriptions", response_model=WebPushSubscriptionsListResponse)
def list_web_push_subscriptions(request: Request) -> WebPushSubscriptionsListResponse:
    user_id = get_user(request).user_id
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: webpush_subscriptions has no `scope` column — push
    # endpoints are inherently per-user device handles, not household
    # content. Per-user `user_id` filter is the correct isolation.
    rows = ms.fetch_all(
        "SELECT id, endpoint, created_at, last_seen_at, last_error, "
        "user_agent, notify_on_signals, notify_on_shared_scope "
        "FROM webpush_subscriptions WHERE user_id = %s ORDER BY id ASC",
        (user_id,),
    )
    out = [_row_to_redacted(dict(r)) for r in rows] if rows else []
    return WebPushSubscriptionsListResponse(subscriptions=out)


@router.patch(
    "/subscriptions/{subscription_id}",
    response_model=WebPushSubscriptionRedacted,
)
def patch_web_push_preferences(
    subscription_id: int,
    body: WebPushSubscriptionPrefsPatch,
    request: Request,
) -> WebPushSubscriptionRedacted:
    user_id = get_user(request).user_id
    ms = config.get_metadata_store()

    sets = []
    params: list = []
    if body.notify_on_signals is not None:
        sets.append("notify_on_signals = %s")
        params.append(body.notify_on_signals)
    if body.notify_on_shared_scope is not None:
        sets.append("notify_on_shared_scope = %s")
        params.append(body.notify_on_shared_scope)

    sql = (
        "UPDATE webpush_subscriptions SET "
        + ", ".join(sets)
        + " WHERE id = %s AND user_id = %s RETURNING "
        " id, endpoint, created_at, last_seen_at, last_error, "
        " user_agent, notify_on_signals, notify_on_shared_scope "
    )

    params.extend([subscription_id, user_id])
    row = ms.fetch_one(sql, tuple(params))
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "subscription_not_found"},
        )
    return _row_to_redacted(dict(row))


@router.post("/subscribe", response_model=WebPushSubscriptionCreated)
def subscribe(
    body: WebPushSubscriptionInput,
    request: Request,
    response: Response,
) -> WebPushSubscriptionCreated:
    _require_webpush_configured()
    user_id = get_user(request).user_id

    ms = config.get_metadata_store()
    endpoint = str(body.endpoint)

    notify_on_signals = False if body.notify_on_signals is None else body.notify_on_signals
    notify_on_shared_scope = (
        True if body.notify_on_shared_scope is None else body.notify_on_shared_scope
    )

    # SCOPE-EXEMPT: webpush_subscriptions has no `scope` column — push
    # endpoints are inherently per-user device handles, not household
    # content. Per-user `user_id` filter is the correct primary key here.
    existing = ms.fetch_one(
        "SELECT id FROM webpush_subscriptions WHERE user_id = %s AND endpoint = %s",
        (user_id, endpoint),
    )
    if existing is not None:
        ms.execute(
            "UPDATE webpush_subscriptions SET last_seen_at = NOW(), "
            " p256dh = %s, auth = %s, user_agent = %s, "
            " notify_on_signals = COALESCE(%s, notify_on_signals), "
            " notify_on_shared_scope = COALESCE(%s, notify_on_shared_scope) "
            "WHERE id = %s",
            (
                body.keys.p256dh,
                body.keys.auth,
                body.user_agent,
                None if body.notify_on_signals is None else body.notify_on_signals,
                None if body.notify_on_shared_scope is None else body.notify_on_shared_scope,
                existing["id"],
            ),
        )
        response.status_code = status.HTTP_200_OK
        return WebPushSubscriptionCreated(id=int(existing["id"]), already_existed=True)

    row = ms.fetch_one(
        "INSERT INTO webpush_subscriptions "
        "(user_id, endpoint, p256dh, auth, user_agent, notify_on_signals, "
        " notify_on_shared_scope) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            user_id,
            endpoint,
            body.keys.p256dh,
            body.keys.auth,
            body.user_agent,
            notify_on_signals,
            notify_on_shared_scope,
        ),
    )
    response.status_code = status.HTTP_201_CREATED
    return WebPushSubscriptionCreated(id=int(row["id"]), already_existed=False)


@router.delete("/subscriptions/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
def unsubscribe(subscription_id: int, request: Request) -> Response:
    _require_webpush_configured()
    user_id = get_user(request).user_id
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: webpush_subscriptions per-user device handle.
    row = ms.fetch_one(
        "DELETE FROM webpush_subscriptions WHERE id = %s AND user_id = %s RETURNING id",
        (subscription_id, user_id),
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "subscription_not_found"},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/test")
def echo_test(request: Request) -> dict:
    """Dev-only: send generic test push payloads, gated by ``WEBPUSH_DEV_ECHO=true``.

    Dispatches ``pywebpush`` when VAPID private key + subject are set — same payload
    contract as Phase 4A sender (**no secrets** in response dict).
    """
    if os.environ.get("WEBPUSH_DEV_ECHO", "").lower() != "true":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found"},
        )
    user_id = get_user(request).user_id
    result = webpush_svc.send_dev_echo_push_for_user(user_id)
    return {
        "sent": result.sent,
        "failed": result.failed,
        "pruned": result.pruned,
        "skipped": result.skipped,
        "disabled_reason": result.disabled_reason,
    }
