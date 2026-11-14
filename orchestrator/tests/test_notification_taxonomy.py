# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Event → type → tier mapping stability."""

from models.notifications import NotificationTier
from models.notifications import NotificationType
from services.notifications.taxonomy import TYPE_TO_TIER
from services.notifications.taxonomy import tier_for_type


def test_signal_received_maps_informational():
    assert TYPE_TO_TIER[NotificationType.SIGNAL_RECEIVED] == NotificationTier.INFORMATIONAL
    assert tier_for_type(NotificationType.SIGNAL_RECEIVED) == NotificationTier.INFORMATIONAL


def test_routine_elevation_maps_action_required():
    assert TYPE_TO_TIER[NotificationType.ROUTINE_ELEVATION] == NotificationTier.ACTION_REQUIRED
    assert tier_for_type(NotificationType.ROUTINE_ELEVATION) == NotificationTier.ACTION_REQUIRED


def test_signal_needs_you_maps_action_required():
    # LUM-648: a needs-you email is push-worthy; the brief is informational.
    assert TYPE_TO_TIER[NotificationType.SIGNAL_NEEDS_YOU] == NotificationTier.ACTION_REQUIRED
    assert TYPE_TO_TIER[NotificationType.SIGNAL_MORNING_BRIEF] == NotificationTier.INFORMATIONAL


def test_every_notification_type_has_a_tier():
    # preferences.get_effective_preferences indexes TYPE_TO_TIER[ntype] for every
    # NotificationType — a missing entry KeyErrors the whole prefs endpoint.
    for ntype in NotificationType:
        assert ntype in TYPE_TO_TIER
