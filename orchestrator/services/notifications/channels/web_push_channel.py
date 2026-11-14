# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Web Push channel adapter — maps notification types to templates."""

from __future__ import annotations

from models.notifications import ChannelDeliveryResult
from models.notifications import ChannelId
from models.notifications import NotificationType
from models.notifications import TypedNotification
from services.webpush import WebPushTemplate

from services import webpush as webpush_svc

_TYPE_TO_TEMPLATE: dict[NotificationType, WebPushTemplate] = {
    NotificationType.ROUTINE_ELEVATION: WebPushTemplate.APPROVAL_REQUIRED,
}

# LUM-648: these types carry a dynamic (runtime) title/body and deep-link /email;
# they use webpush.send_dynamic_to_user (gated on notify_on_signals) rather than a
# fixed template. Serialization still funnels through build_safe_push_json_from_dict.
_DYNAMIC_BODY_TYPES: frozenset[NotificationType] = frozenset(
    {NotificationType.SIGNAL_NEEDS_YOU, NotificationType.SIGNAL_MORNING_BRIEF}
)


def _result_from_send(result) -> ChannelDeliveryResult:
    if result.disabled_reason:
        return ChannelDeliveryResult(
            channel=ChannelId.WEB_PUSH, status="skipped", reason=result.disabled_reason
        )
    if result.sent > 0:
        return ChannelDeliveryResult(channel=ChannelId.WEB_PUSH, status="delivered")
    if result.failed > 0:
        return ChannelDeliveryResult(
            channel=ChannelId.WEB_PUSH, status="failed", reason="send_failed"
        )
    return ChannelDeliveryResult(
        channel=ChannelId.WEB_PUSH, status="skipped", reason="no_subscriptions"
    )


class WebPushChannel:
    channel_id = ChannelId.WEB_PUSH

    def deliver(self, notification: TypedNotification) -> ChannelDeliveryResult:
        if notification.notification_type in _DYNAMIC_BODY_TYPES:
            # VAPID guard must apply to this branch too (Sonnet R2 P2) — the fixed
            # path checks it after the template lookup, which we bypass here.
            if not webpush_svc.vapid_send_configured():
                return ChannelDeliveryResult(
                    channel=ChannelId.WEB_PUSH,
                    status="skipped",
                    reason="vapid_not_configured",
                )
            meta = notification.metadata or {}
            url = meta.get("target_path") or "/email"
            # async_send: the slow pywebpush network I/O runs on webpush._executor,
            # so emit() never blocks its caller on the network — critical on the
            # ingest path (signal_processor._notify) where a needs-you email must not
            # stall signal processing on a slow push endpoint (LUM-662).
            result = webpush_svc.send_dynamic_to_user(
                notification.user_id,
                notification.title,
                notification.body,
                url,
                async_send=True,
            )
            return _result_from_send(result)

        template = _TYPE_TO_TEMPLATE.get(notification.notification_type)
        if template is None:
            return ChannelDeliveryResult(
                channel=ChannelId.WEB_PUSH,
                status="skipped",
                reason="template_not_mapped",
            )

        if not webpush_svc.vapid_send_configured():
            return ChannelDeliveryResult(
                channel=ChannelId.WEB_PUSH,
                status="skipped",
                reason="vapid_not_configured",
            )

        result = webpush_svc.send_templates_to_user(notification.user_id, template)
        return _result_from_send(result)
