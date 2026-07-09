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


class WebPushChannel:
    channel_id = ChannelId.WEB_PUSH

    def deliver(self, notification: TypedNotification) -> ChannelDeliveryResult:
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
        if result.disabled_reason:
            return ChannelDeliveryResult(
                channel=ChannelId.WEB_PUSH,
                status="skipped",
                reason=result.disabled_reason,
            )
        if result.sent > 0:
            return ChannelDeliveryResult(channel=ChannelId.WEB_PUSH, status="delivered")
        if result.failed > 0:
            return ChannelDeliveryResult(
                channel=ChannelId.WEB_PUSH,
                status="failed",
                reason="send_failed",
            )
        return ChannelDeliveryResult(
            channel=ChannelId.WEB_PUSH,
            status="skipped",
            reason="no_subscriptions",
        )
