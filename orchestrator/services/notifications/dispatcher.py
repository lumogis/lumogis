# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unified notification dispatcher (ADR 077 decision sequence)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from datetime import time
from datetime import timezone
from zoneinfo import ZoneInfo

import hooks
import structlog
from events import Event
from models.notifications import ChannelDeliveryResult
from models.notifications import ChannelId
from models.notifications import DispatchResult
from models.notifications import NotificationTier
from models.notifications import NotificationType
from models.notifications import TypedNotification
from services.notifications.preferences import _load_sparse_prefs
from services.notifications.preferences import _load_user_settings
from services.notifications.preferences import is_channel_enabled_for_emit
from services.notifications.preferences import load_tier_policies
from services.notifications.taxonomy import tier_for_type

_log = logging.getLogger("lumogis.notifications")
_audit = structlog.get_logger("lumogis.audit")

_PUSH_CHANNELS = frozenset({ChannelId.NTFY, ChannelId.WEB_PUSH})


def is_in_quiet_hours(now_local: datetime, start: time | None, end: time | None) -> bool:
    if start is None or end is None:
        return False
    t = now_local.time()
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end


def _resolve_timezone(settings: dict) -> ZoneInfo:
    tz_name = settings.get("timezone")
    if not tz_name:
        _log.warning("notification_timezone_fallback reason=%s", "missing_timezone")
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(str(tz_name))
    except Exception:
        _log.warning(
            "notification_timezone_fallback reason=%s timezone=%s",
            "invalid_timezone",
            tz_name,
        )
        return ZoneInfo("UTC")


def resolve_effective_channels(
    user_id: str,
    notification_type: NotificationType,
    tier: NotificationTier,
    *,
    sparse: dict | None = None,
    tier_policies: dict | None = None,
) -> list[ChannelId]:
    policies = tier_policies or load_tier_policies()
    policy = policies[tier]
    if sparse is None:
        sparse = _load_sparse_prefs(user_id)
    channels: list[ChannelId] = []
    for ch in policy.default_channels:
        if is_channel_enabled_for_emit(
            user_id,
            notification_type,
            ch,
            tier_policy=policy,
            sparse=sparse,
        ):
            channels.append(ch)
    return channels


def _compute_outcome(results: list[ChannelDeliveryResult]) -> str:
    if not results:
        return "all_skipped"
    delivered = sum(1 for r in results if r.status == "delivered")
    failed = sum(1 for r in results if r.status == "failed")
    skipped = sum(1 for r in results if r.status == "skipped")
    if delivered and not failed and not skipped:
        return "delivered"
    if delivered and (failed or skipped):
        return "partial"
    if delivered == 0 and failed > 0:
        return "failed"
    return "all_skipped"


def emit(notification: TypedNotification) -> DispatchResult:
    if not notification.user_id or not str(notification.user_id).strip():
        _log.warning("notification emit rejected: empty user_id")
        return DispatchResult(
            emit_id=notification.emit_id or "",
            user_id="",
            notification_type=notification.notification_type,
            tier=tier_for_type(notification.notification_type, override=notification.tier),
            channels=[],
            outcome="failed",
        )

    emit_id = notification.emit_id or str(uuid.uuid4())
    notification = notification.model_copy(update={"emit_id": emit_id})

    tier = tier_for_type(notification.notification_type, override=notification.tier)
    notification = notification.model_copy(update={"tier": tier})

    tier_policies = load_tier_policies()
    tier_policy = tier_policies[tier]
    sparse = _load_sparse_prefs(notification.user_id)
    settings = _load_user_settings(notification.user_id)
    tz = _resolve_timezone(settings)
    now_local = datetime.now(timezone.utc).astimezone(tz)
    in_quiet = is_in_quiet_hours(
        now_local,
        settings.get("quiet_hours_start"),
        settings.get("quiet_hours_end"),
    )

    candidate_channels = list(tier_policy.default_channels)
    results: list[ChannelDeliveryResult] = []
    channels_to_deliver: list[ChannelId] = []
    quiet_skipped_push: list[ChannelId] = []

    import config

    channel_adapters = config.get_notification_channels()

    for ch in candidate_channels:
        if not is_channel_enabled_for_emit(
            notification.user_id,
            notification.notification_type,
            ch,
            tier_policy=tier_policy,
            sparse=sparse,
        ):
            results.append(
                ChannelDeliveryResult(channel=ch, status="skipped", reason="preference_disabled")
            )
            continue

        if (
            in_quiet
            and not tier_policy.bypass_quiet_hours
            and ch in _PUSH_CHANNELS
        ):
            quiet_skipped_push.append(ch)
            results.append(
                ChannelDeliveryResult(channel=ch, status="skipped", reason="quiet_hours")
            )
            continue

        channels_to_deliver.append(ch)

    if quiet_skipped_push and tier_policy.bypass_quiet_hours:
        for ch in quiet_skipped_push:
            # Replace quiet_hours skip with delivery attempt.
            results = [r for r in results if not (r.channel == ch and r.reason == "quiet_hours")]
            channels_to_deliver.append(ch)
        delivered_names = [c.value for c in channels_to_deliver]
        _audit.info(
            "notification.quiet_hours_bypass",
            emit_id=emit_id,
            user_id=notification.user_id,
            notification_type=notification.notification_type.value,
            tier=tier.value,
            channels_delivered=delivered_names,
            occurred_at=datetime.now(timezone.utc).isoformat(),
        )

    delivery_results: list[ChannelDeliveryResult] = []
    for ch in channels_to_deliver:
        adapter = channel_adapters.get(ch)
        if adapter is None:
            delivery_results.append(
                ChannelDeliveryResult(channel=ch, status="skipped", reason="adapter_missing")
            )
            continue
        try:
            delivery_results.append(adapter.deliver(notification))
        except Exception:
            _log.warning("channel deliver failed channel=%s", ch.value, exc_info=True)
            delivery_results.append(
                ChannelDeliveryResult(channel=ch, status="failed", reason="delivery_error")
            )

    results.extend(delivery_results)

    push_delivered = any(
        r.channel in _PUSH_CHANNELS and r.status == "delivered" for r in results
    )
    if tier == NotificationTier.URGENT and not push_delivered:
        in_app_delivered = any(
            r.channel == ChannelId.IN_APP and r.status == "delivered" for r in results
        )
        if not in_app_delivered:
            adapter = channel_adapters.get(ChannelId.IN_APP)
            if adapter is not None and ChannelId.IN_APP in tier_policy.default_channels:
                if is_channel_enabled_for_emit(
                    notification.user_id,
                    notification.notification_type,
                    ChannelId.IN_APP,
                    tier_policy=tier_policy,
                    sparse=sparse,
                ):
                    try:
                        floor_result = adapter.deliver(notification)
                        results.append(floor_result)
                    except Exception:
                        _log.warning("urgent in_app floor failed", exc_info=True)
        _log.warning(
            "notification_urgent_zero_push_channels emit_id=%s user_id=%s notification_type=%s",
            emit_id,
            notification.user_id,
            notification.notification_type.value,
        )

    outcome = _compute_outcome(results)
    return DispatchResult(
        emit_id=emit_id,
        user_id=notification.user_id,
        notification_type=notification.notification_type,
        tier=tier,
        channels=results,
        outcome=outcome,  # type: ignore[arg-type]
    )


def _on_routine_elevation_ready(**kwargs) -> None:
    user_id = kwargs.get("user_id")
    if not user_id:
        return
    emit(
        TypedNotification(
            user_id=str(user_id),
            notification_type=NotificationType.ROUTINE_ELEVATION,
            title="Lumogis",
            body="Approval required",
            metadata={
                "connector": kwargs.get("connector"),
                "action_type": kwargs.get("action_type"),
                "approval_count": kwargs.get("approval_count"),
            },
        )
    )


def register_hook_listeners() -> None:
    hooks.register(Event.ROUTINE_ELEVATION_READY, _on_routine_elevation_ready)
    _log.info("notification dispatcher hook listeners registered")
