# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Read/write notification preference store + tier policy (LUM-93)."""

from __future__ import annotations

import logging
from datetime import time
from typing import Any

import config
from fastapi import HTTPException
from models.notifications import ChannelId
from models.notifications import NotificationPreferenceCell
from models.notifications import NotificationPreferencePatchItem
from models.notifications import NotificationPreferencesPatch
from models.notifications import NotificationPreferencesResponse
from models.notifications import NotificationTier
from models.notifications import NotificationTierPolicyPatch
from models.notifications import NotificationTierPolicyRow
from models.notifications import NotificationType
from models.notifications import NotificationTypePrefsRow
from services.notifications.taxonomy import TYPE_TO_TIER

_log = logging.getLogger(__name__)

_ALL_CHANNELS = list(ChannelId)


def _time_to_iso(t: time | None) -> str | None:
    if t is None:
        return None
    return t.strftime("%H:%M:%S")


def _parse_channel_list(raw: list[str] | None) -> list[ChannelId]:
    if not raw:
        return []
    return [ChannelId(c) for c in raw]


def load_tier_policies() -> dict[NotificationTier, NotificationTierPolicyRow]:
    cached = config.get_tier_policies_cache()
    if cached is not None:
        return cached

    ms = config.get_metadata_store()
    rows = ms.fetch_all(
        "SELECT tier, bypass_quiet_hours, default_channels "
        "FROM notification_tier_policy ORDER BY tier"
    )
    result: dict[NotificationTier, NotificationTierPolicyRow] = {}
    for row in rows or []:
        tier = NotificationTier(row["tier"])
        result[tier] = NotificationTierPolicyRow(
            tier=tier,
            bypass_quiet_hours=bool(row["bypass_quiet_hours"]),
            default_channels=_parse_channel_list(row.get("default_channels")),
        )
    # Seed defaults when table empty (unit tests / pre-migration).
    if not result:
        for tier, channels in (
            (NotificationTier.URGENT, [ChannelId.NTFY, ChannelId.WEB_PUSH, ChannelId.IN_APP]),
            (
                NotificationTier.ACTION_REQUIRED,
                [ChannelId.NTFY, ChannelId.WEB_PUSH, ChannelId.IN_APP],
            ),
            (
                NotificationTier.INFORMATIONAL,
                [ChannelId.NTFY, ChannelId.WEB_PUSH, ChannelId.IN_APP],
            ),
            (NotificationTier.BACKGROUND, [ChannelId.IN_APP]),
        ):
            result[tier] = NotificationTierPolicyRow(
                tier=tier,
                bypass_quiet_hours=(tier == NotificationTier.URGENT),
                default_channels=channels,
            )
    config.set_tier_policies_cache(result)
    return result


def _load_sparse_prefs(user_id: str) -> dict[tuple[NotificationType, ChannelId], bool]:
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: notification_preferences is per-user settings (no scope column).
    rows = ms.fetch_all(
        "SELECT notification_type, channel, enabled FROM notification_preferences "
        "WHERE user_id = %s",
        (user_id,),
    )
    out: dict[tuple[NotificationType, ChannelId], bool] = {}
    for row in rows or []:
        try:
            ntype = NotificationType(row["notification_type"])
            channel = ChannelId(row["channel"])
        except ValueError:
            continue
        out[(ntype, channel)] = bool(row["enabled"])
    return out


def _load_user_settings(user_id: str) -> dict[str, Any]:
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: notification_user_settings is per-user settings (no scope column).
    row = ms.fetch_one(
        "SELECT timezone, quiet_hours_start, quiet_hours_end "
        "FROM notification_user_settings WHERE user_id = %s",
        (user_id,),
    )
    return dict(row) if row else {}


def _build_cell(
    ntype: NotificationType,
    channel: ChannelId,
    tier_policy: NotificationTierPolicyRow,
    sparse: dict[tuple[NotificationType, ChannelId], bool],
) -> NotificationPreferenceCell:
    in_tier = channel in tier_policy.default_channels
    tier_default = in_tier
    stored = sparse.get((ntype, channel))
    if stored is None:
        enabled = tier_default
    else:
        enabled = stored
    effective = enabled and in_tier
    mutable = in_tier
    return NotificationPreferenceCell(
        channel=channel,
        enabled=enabled,
        effective=effective,
        mutable=mutable,
        tier_default=tier_default,
    )


def get_effective_preferences(user_id: str) -> NotificationPreferencesResponse:
    tier_policies = load_tier_policies()
    sparse = _load_sparse_prefs(user_id)
    settings = _load_user_settings(user_id)

    type_rows: list[NotificationTypePrefsRow] = []
    for ntype in NotificationType:
        tier = TYPE_TO_TIER[ntype]
        policy = tier_policies[tier]
        cells = [_build_cell(ntype, ch, policy, sparse) for ch in _ALL_CHANNELS]
        type_rows.append(
            NotificationTypePrefsRow(
                notification_type=ntype,
                tier=tier,
                channels=cells,
            )
        )

    return NotificationPreferencesResponse(
        types=type_rows,
        timezone=settings.get("timezone"),
        quiet_hours_start=_time_to_iso(settings.get("quiet_hours_start")),
        quiet_hours_end=_time_to_iso(settings.get("quiet_hours_end")),
    )


def _validate_patch_item(
    item: NotificationPreferencePatchItem,
    tier_policies: dict[NotificationTier, NotificationTierPolicyRow],
) -> None:
    tier = TYPE_TO_TIER[item.notification_type]
    policy = tier_policies[tier]
    if item.enabled and item.channel not in policy.default_channels:
        raise HTTPException(
            status_code=422,
            detail="preference_channel_not_in_tier",
        )


def patch_preferences(
    user_id: str,
    patch: NotificationPreferencesPatch,
) -> NotificationPreferencesResponse:
    tier_policies = load_tier_policies()

    # Last-wins for duplicate (type, channel) in one body.
    merged: dict[tuple[NotificationType, ChannelId], NotificationPreferencePatchItem] = {}
    for item in patch.preferences:
        merged[(item.notification_type, item.channel)] = item

    ms = config.get_metadata_store()
    for item in merged.values():
        _validate_patch_item(item, tier_policies)
        ms.execute(
            "INSERT INTO notification_preferences "
            "(user_id, notification_type, channel, enabled, updated_at) "
            "VALUES (%s, %s, %s, %s, NOW()) "
            "ON CONFLICT (user_id, notification_type, channel) DO UPDATE SET "
            "enabled = EXCLUDED.enabled, updated_at = NOW()",
            (
                user_id,
                item.notification_type.value,
                item.channel.value,
                item.enabled,
            ),
        )

    return get_effective_preferences(user_id)


def get_tier_policies() -> list[NotificationTierPolicyRow]:
    policies = load_tier_policies()
    return [policies[t] for t in NotificationTier]


def _validate_default_channels(channels: list[ChannelId]) -> None:
    if not channels:
        raise HTTPException(status_code=422, detail="tier_policy_invalid_channels")
    seen: set[ChannelId] = set()
    for ch in channels:
        if ch not in ChannelId:
            raise HTTPException(status_code=422, detail="tier_policy_invalid_channels")
        if ch in seen:
            raise HTTPException(status_code=422, detail="tier_policy_invalid_channels")
        seen.add(ch)


def patch_tier_policy(
    tier: NotificationTier,
    patch: NotificationTierPolicyPatch,
    *,
    actor_user_id: str,
) -> NotificationTierPolicyRow:
    current = load_tier_policies()[tier]
    bypass = (
        patch.bypass_quiet_hours
        if patch.bypass_quiet_hours is not None
        else current.bypass_quiet_hours
    )
    if patch.default_channels is not None:
        _validate_default_channels(patch.default_channels)
        channels = patch.default_channels
    else:
        channels = current.default_channels

    channel_values = [c.value for c in channels]
    ms = config.get_metadata_store()
    ms.execute(
        "INSERT INTO notification_tier_policy (tier, bypass_quiet_hours, default_channels) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (tier) DO UPDATE SET "
        "bypass_quiet_hours = EXCLUDED.bypass_quiet_hours, "
        "default_channels = EXCLUDED.default_channels",
        (tier.value, bypass, channel_values),
    )
    config.invalidate_tier_policy_cache()

    import structlog

    audit = structlog.get_logger("lumogis.audit")
    audit.info(
        "notification.tier_policy_changed",
        tier=tier.value,
        actor_user_id=actor_user_id,
        bypass_quiet_hours=bypass,
        default_channels=channel_values,
    )

    return NotificationTierPolicyRow(
        tier=tier,
        bypass_quiet_hours=bypass,
        default_channels=channels,
    )


def is_channel_enabled_for_emit(
    user_id: str,
    notification_type: NotificationType,
    channel: ChannelId,
    *,
    tier_policy: NotificationTierPolicyRow,
    sparse: dict[tuple[NotificationType, ChannelId], bool] | None = None,
) -> bool:
    if channel not in tier_policy.default_channels:
        return False
    if sparse is None:
        sparse = _load_sparse_prefs(user_id)
    stored = sparse.get((notification_type, channel))
    if stored is None:
        return True
    return stored
