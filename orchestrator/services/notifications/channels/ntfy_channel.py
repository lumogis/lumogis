# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""ntfy channel adapter — wraps existing :func:`config.get_notifier`."""

from __future__ import annotations

import os

import config
from models.notifications import ChannelDeliveryResult
from models.notifications import ChannelId
from models.notifications import NotificationTier
from models.notifications import TypedNotification
from services.notifications.taxonomy import NTFY_PRIORITY_BY_TIER
from services.notifications.taxonomy import tier_for_type


class NtfyChannel:
    channel_id = ChannelId.NTFY

    def deliver(self, notification: TypedNotification) -> ChannelDeliveryResult:
        backend = os.environ.get("NOTIFIER_BACKEND", "none")
        if backend != "ntfy":
            return ChannelDeliveryResult(
                channel=ChannelId.NTFY,
                status="skipped",
                reason="deployment_unavailable",
            )

        from services.ntfy_runtime import load_ntfy_runtime_config
        from services import connector_credentials as ccs

        try:
            load_ntfy_runtime_config(notification.user_id)
        except ccs.ConnectorNotConfigured:
            return ChannelDeliveryResult(
                channel=ChannelId.NTFY,
                status="skipped",
                reason="connector_not_configured",
            )
        except ccs.CredentialUnavailable:
            return ChannelDeliveryResult(
                channel=ChannelId.NTFY,
                status="skipped",
                reason="connector_not_configured",
            )

        tier = tier_for_type(notification.notification_type, override=notification.tier)
        if tier == NotificationTier.BACKGROUND:
            return ChannelDeliveryResult(
                channel=ChannelId.NTFY,
                status="skipped",
                reason="tier_not_supported",
            )

        priority = NTFY_PRIORITY_BY_TIER.get(tier, 0.5)
        try:
            ok = config.get_notifier().notify(
                notification.title,
                notification.body,
                priority,
                user_id=notification.user_id,
            )
        except Exception:
            return ChannelDeliveryResult(
                channel=ChannelId.NTFY,
                status="failed",
                reason="delivery_error",
            )

        if ok:
            return ChannelDeliveryResult(channel=ChannelId.NTFY, status="delivered")
        return ChannelDeliveryResult(
            channel=ChannelId.NTFY,
            status="failed",
            reason="delivery_rejected",
        )
