# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Stable notification type → tier mapping (ADR 077)."""

from __future__ import annotations

from models.notifications import NotificationTier
from models.notifications import NotificationType

TYPE_TO_TIER: dict[NotificationType, NotificationTier] = {
    NotificationType.ROUTINE_ELEVATION: NotificationTier.ACTION_REQUIRED,
    NotificationType.SIGNAL_RECEIVED: NotificationTier.INFORMATIONAL,
    NotificationType.SIGNAL_DIGEST: NotificationTier.INFORMATIONAL,
    NotificationType.ACTION_EXECUTED: NotificationTier.ACTION_REQUIRED,
    NotificationType.SECURITY_ALERT: NotificationTier.URGENT,
    NotificationType.CONSOLIDATION_DONE: NotificationTier.BACKGROUND,
}

NTFY_PRIORITY_BY_TIER: dict[NotificationTier, float] = {
    NotificationTier.URGENT: 1.0,
    NotificationTier.ACTION_REQUIRED: 0.75,
    NotificationTier.INFORMATIONAL: 0.5,
}


def tier_for_type(
    notification_type: NotificationType,
    *,
    override: NotificationTier | None = None,
) -> NotificationTier:
    if override is not None:
        return override
    return TYPE_TO_TIER[notification_type]
