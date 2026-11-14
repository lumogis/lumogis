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
    # LUM-648: a needs-you email is ACTION_REQUIRED (push-worthy); the daily
    # brief is INFORMATIONAL. preferences.get_effective_preferences reads
    # TYPE_TO_TIER[ntype] directly for every NotificationType, so both entries
    # are mandatory the moment the enum members exist (else KeyError on
    # GET /api/v1/me/notification-preferences).
    NotificationType.SIGNAL_NEEDS_YOU: NotificationTier.ACTION_REQUIRED,
    NotificationType.SIGNAL_MORNING_BRIEF: NotificationTier.INFORMATIONAL,
    # LUM-758: INFORMATIONAL, not ACTION_REQUIRED — the decision is already made
    # and applied by the time this fires. Marking it action-required would imply an
    # approval that does not exist, and is how a notice earns being muted.
    NotificationType.EGRESS_ALLOWLIST_WIDENED: NotificationTier.INFORMATIONAL,
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
