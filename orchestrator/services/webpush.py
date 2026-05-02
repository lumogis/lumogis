# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Web Push outbound delivery (browser subscriptions + VAPID + pywebpush).

Phase 4A wires the sender plus a narrow hook (``ROUTINE_ELEVATION_READY``).
Parallel :mod:`ports.notifier` / ntfy is **unchanged**.

**Pref columns** (migration 019):

* ``notify_on_signals`` / ``notify_on_shared_scope`` gate *future*
  signal-digest pushes. **ROUTINE_ELEVATION_READY** is **not** a signal
  digest — these prefs are **ignored** for the approval template until a
  separate ``SIGNAL_RECEIVED`` path exists.

**Deferred:** ``ACTION_EXECUTED`` — hooks carry connector/action identifiers
that would leak into generic UX if mirrored; callers should not pass raw
event kwargs into payloads (see extraction doc).

Do **not** log subscription endpoints, key material, or push payload bodies.
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from typing import Any

import hooks
from events import Event

import config

_log = logging.getLogger(__name__)

_MAX_LAST_ERROR_CHARS = 512

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="webpush-out")


class WebPushTemplate(str, Enum):
    """Stable, caller-only templates — no external string injection."""

    APPROVAL_REQUIRED = "approval_required"
    DEV_TEST = "dev_test"


@dataclass(frozen=True)
class WebPushPayload:
    title: str
    body: str
    url: str = "/"


@dataclass
class WebPushSendResult:
    sent: int = 0
    failed: int = 0
    pruned: int = 0
    skipped: int = 0
    disabled_reason: str | None = None


_TEMPLATES: dict[WebPushTemplate, WebPushPayload] = {
    WebPushTemplate.APPROVAL_REQUIRED: WebPushPayload(
        title="Lumogis",
        body="Approval required",
        url="/approvals",
    ),
    WebPushTemplate.DEV_TEST: WebPushPayload(
        title="Lumogis",
        body="Test from Lumogis",
        url="/",
    ),
}


def build_web_push_payload(template: WebPushTemplate, url: str | None = None) -> str:
    """Return JSON string for pywebpush ``data=`` (UTF-8). **No** event fields."""
    spec = _TEMPLATES[template]
    effective_url = url if url is not None else spec.url
    obj = {"title": spec.title, "body": spec.body, "url": effective_url}
    return build_safe_push_json_from_dict(obj)


def build_safe_push_json_from_dict(obj: dict[str, Any]) -> str:
    """Serialize only title/body/url keys (defensive). Used by tests."""
    allowed = {"title", "body", "url"}
    if set(obj.keys()) - allowed:
        raise ValueError("webpush payload may only contain title, body, url")
    for k in ("title", "body", "url"):
        if k not in obj:
            raise ValueError(f"webpush payload missing {k}")
    if not isinstance(obj["title"], str) or not isinstance(obj["body"], str):
        raise TypeError("title and body must be str")
    if not isinstance(obj["url"], str):
        raise TypeError("url must be str")
    return json.dumps(
        {"title": obj["title"], "body": obj["body"], "url": obj["url"]},
        ensure_ascii=False,
    )


def vapid_send_configured() -> bool:
    """True when outbound Web Push can authenticate (private key + subject)."""
    return bool(
        (os.environ.get("WEBPUSH_VAPID_PRIVATE_KEY") or "").strip()
        and (os.environ.get("WEBPUSH_VAPID_SUBJECT") or "").strip()
    )


def _vapid_claims() -> dict[str, str] | None:
    subj = (os.environ.get("WEBPUSH_VAPID_SUBJECT") or "").strip()
    if not subj:
        return None
    return {"sub": subj}


def _vapid_private_key() -> str | None:
    key = (os.environ.get("WEBPUSH_VAPID_PRIVATE_KEY") or "").strip()
    return key or None


def _dispatch_web_push(
    *,
    subscription_info: dict[str, Any],
    data: bytes,
) -> None:
    """Delegate to pywebpush (patch point for tests)."""
    from pywebpush import webpush

    priv = _vapid_private_key()
    claims = _vapid_claims()
    if not priv or not claims:
        raise RuntimeError("webpush: VAPID private key or subject missing")

    webpush(
        subscription_info=subscription_info,
        data=data,
        vapid_private_key=priv,
        vapid_claims=claims,
        ttl=86_400,
        timeout=10,
    )


def _http_status_from_push_error(exc: BaseException) -> int | None:
    resp = getattr(exc, "response", None)
    if resp is not None:
        code = getattr(resp, "status_code", None)
        if isinstance(code, int):
            return code
    return None


def _truncate_error(msg: str) -> str:
    msg = " ".join(msg.split())
    if len(msg) > _MAX_LAST_ERROR_CHARS:
        return msg[: _MAX_LAST_ERROR_CHARS - 3] + "..."
    return msg


def _non_sensitive_failure_reason(exc: BaseException) -> str:
    status = _http_status_from_push_error(exc)
    if status is not None:
        return f"http_{status}"
    name = type(exc).__name__
    return _truncate_error(name)


def prune_invalid_subscription(subscription_id: int, _reason: str | None = None) -> None:
    """Delete a subscription row (410/404 / gone endpoint)."""
    ms = config.get_metadata_store()
    ms.execute(
        "DELETE FROM webpush_subscriptions WHERE id = %s",
        (subscription_id,),
    )
    _log.info(
        "webpush subscription pruned id=%s",
        subscription_id,
    )


def _update_last_error(subscription_id: int, message: str) -> None:
    ms = config.get_metadata_store()
    ms.execute(
        "UPDATE webpush_subscriptions SET last_error = %s WHERE id = %s",
        (_truncate_error(message), subscription_id),
    )


def send_web_push_to_subscription(
    subscription_id: int,
    subscription_info: dict[str, Any],
    payload_json: str,
) -> tuple[str, int | None]:
    """Send to one row. Returns ``('ok'|'pruned'|'failed', None)``."""
    try:
        _dispatch_web_push(
            subscription_info=subscription_info,
            data=payload_json.encode("utf-8"),
        )
        ms = config.get_metadata_store()
        ms.execute(
            "UPDATE webpush_subscriptions SET last_error = NULL WHERE id = %s",
            (subscription_id,),
        )
        return "ok", None
    except Exception as exc:
        status = _http_status_from_push_error(exc)
        if status in (404, 410):
            prune_invalid_subscription(subscription_id)
            return "pruned", status
        reason = _non_sensitive_failure_reason(exc)
        _update_last_error(subscription_id, reason)
        _log.warning(
            "webpush send failed subscription_id=%s class=%s",
            subscription_id,
            type(exc).__name__,
        )
        return "failed", status


def _list_subscriptions_for_user(user_id: str) -> list[dict[str, Any]]:
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: webpush_subscriptions has no `scope` column — push
    # endpoints are inherently per-user device handles, not household
    # content. Per-user `user_id` filter is the correct isolation.
    rows = ms.fetch_all(
        "SELECT id, endpoint, p256dh, auth, notify_on_signals, "
        "notify_on_shared_scope FROM webpush_subscriptions "
        "WHERE user_id = %s",
        (user_id,),
    )
    return list(rows) if rows else []


def send_templates_to_user(
    user_id: str,
    template: WebPushTemplate,
    *,
    url_override: str | None = None,
) -> WebPushSendResult:
    """Fan out a template to all of ``user_id``'s subscriptions."""
    if not isinstance(user_id, str) or not user_id:
        return WebPushSendResult(skipped=0, disabled_reason="invalid_user_id")

    if not vapid_send_configured():
        return WebPushSendResult(disabled_reason="webpush_vapid_incomplete")

    payload_json = build_web_push_payload(template, url=url_override)
    rows = _list_subscriptions_for_user(user_id)
    if not rows:
        return WebPushSendResult()

    result = WebPushSendResult()
    for row in rows:
        sid = int(row["id"])
        sub_info = {
            "endpoint": row["endpoint"],
            "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
        }
        kind, _status = send_web_push_to_subscription(sid, sub_info, payload_json)
        if kind == "ok":
            result.sent += 1
        elif kind == "pruned":
            result.pruned += 1
        else:
            result.failed += 1
    return result


def send_dev_echo_push_for_user(user_id: str) -> WebPushSendResult:
    """``GET /api/v1/notifications/test`` — generic body, dev-gated by route."""
    return send_templates_to_user(user_id, WebPushTemplate.DEV_TEST)


def send_test_push_to_user(user_id: str) -> WebPushSendResult:
    """Alias for tests / admin scripts (same as dev echo template)."""
    return send_dev_echo_push_for_user(user_id)


def _on_routine_elevation_ready_web_push(**kwargs: Any) -> None:
    """Hook: ``ROUTINE_ELEVATION_READY`` — ignore connector/action kwargs."""
    user_id = kwargs.get("user_id")
    if not user_id:
        return

    def _run() -> None:
        try:
            send_templates_to_user(
                str(user_id),
                WebPushTemplate.APPROVAL_REQUIRED,
            )
        except Exception:
            _log.exception("webpush routine_elevation hook failed")

    _executor.submit(_run)


def register_web_push_hooks() -> None:
    """Register Web Push listeners. Call from ``main`` after SSE hooks."""
    hooks.register(Event.ROUTINE_ELEVATION_READY, _on_routine_elevation_ready_web_push)
    _log.info("Web Push hooks registered (ROUTINE_ELEVATION_READY)")


def shutdown_web_push_executor() -> None:
    """Release thread pool; allocate a fresh pool for test `TestClient` re-starts."""
    global _executor
    try:
        _executor.shutdown(wait=True, cancel_futures=False)
    except Exception:
        _log.debug("webpush executor shutdown", exc_info=True)
    _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="webpush-out")
