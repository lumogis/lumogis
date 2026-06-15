# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""One-time OR-collapse of webpush_subscriptions.notify_on_* into sparse prefs."""

from __future__ import annotations

import logging

import config
from models.notifications import ChannelId
from models.notifications import NotificationType

_log = logging.getLogger(__name__)

_SEEDER_FLAG_KEY = "webpush_prefs_seeded_v1"


def _seeder_already_ran() -> bool:
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT value FROM app_settings WHERE key = %s",
        (_SEEDER_FLAG_KEY,),
    )
    return bool(row and row.get("value") == "1")


def _mark_seeder_done() -> None:
    ms = config.get_metadata_store()
    ms.execute(
        "INSERT INTO app_settings (key, value, updated_at) VALUES (%s, %s, NOW()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
        (_SEEDER_FLAG_KEY, "1"),
    )


def _sparse_row_exists(user_id: str, ntype: NotificationType, channel: ChannelId) -> bool:
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: notification_preferences is per-user settings (no scope column).
    row = ms.fetch_one(
        "SELECT 1 FROM notification_preferences "
        "WHERE user_id = %s AND notification_type = %s AND channel = %s",
        (user_id, ntype.value, channel.value),
    )
    return row is not None


def _upsert_pref(user_id: str, ntype: NotificationType, enabled: bool) -> None:
    ms = config.get_metadata_store()
    ms.execute(
        "INSERT INTO notification_preferences "
        "(user_id, notification_type, channel, enabled, updated_at) "
        "VALUES (%s, %s, %s, %s, NOW()) "
        "ON CONFLICT (user_id, notification_type, channel) DO UPDATE SET "
        "enabled = EXCLUDED.enabled, updated_at = NOW()",
        (user_id, ntype.value, ChannelId.WEB_PUSH.value, enabled),
    )


def _seed_user(user_id: str) -> None:
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: webpush_subscriptions is per-user delivery state (no scope column).
    rows = ms.fetch_all(
        "SELECT notify_on_signals, notify_on_shared_scope FROM webpush_subscriptions "
        "WHERE user_id = %s",
        (user_id,),
    )
    any_signals = any(bool(r.get("notify_on_signals")) for r in (rows or []))
    any_shared = any(bool(r.get("notify_on_shared_scope")) for r in (rows or []))

    signal_types = (NotificationType.SIGNAL_RECEIVED, NotificationType.SIGNAL_DIGEST)
    shared_types = (NotificationType.ROUTINE_ELEVATION, NotificationType.ACTION_EXECUTED)

    for ntype in signal_types:
        if _sparse_row_exists(user_id, ntype, ChannelId.WEB_PUSH):
            continue
        _upsert_pref(user_id, ntype, any_signals)

    for ntype in shared_types:
        if _sparse_row_exists(user_id, ntype, ChannelId.WEB_PUSH):
            continue
        _upsert_pref(user_id, ntype, any_shared)


def run_if_needed() -> None:
    """Idempotent startup seeder — fail-open on error."""
    try:
        if _seeder_already_ran():
            return
        ms = config.get_metadata_store()
        user_rows = ms.fetch_all(
            "SELECT DISTINCT user_id FROM webpush_subscriptions",
            (),
        )
        for row in user_rows or []:
            uid = row.get("user_id")
            if uid:
                _seed_user(str(uid))
        _mark_seeder_done()
        _log.info("webpush_prefs_seeder: completed")
    except Exception:
        _log.exception("webpush_prefs_seeder: failed — tier defaults apply until manual re-run")
