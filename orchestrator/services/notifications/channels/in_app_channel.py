# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""In-app SSE channel — sole producer for signal/routine notification events."""

from __future__ import annotations

from models.notifications import ChannelDeliveryResult
from models.notifications import ChannelId
from models.notifications import NotificationType
from models.notifications import TypedNotification
from routes import events as events_routes


def _sse_payload(notification: TypedNotification) -> tuple[str, dict]:
    meta = dict(notification.metadata or {})
    emit_id = notification.emit_id
    ntype = notification.notification_type

    if ntype == NotificationType.SIGNAL_RECEIVED:
        data = {
            "signal_id": meta.get("signal_id"),
            "title": notification.title,
            "url": meta.get("url"),
            "importance_score": meta.get("importance_score"),
            "relevance_score": meta.get("relevance_score"),
            "emit_id": emit_id,
        }
        return "signal_received", data

    if ntype == NotificationType.SIGNAL_DIGEST:
        data = {
            "digest_id": meta.get("digest_id"),
            "signal_count": meta.get("signal_count"),
            "summary": meta.get("summary", notification.body),
            "emit_id": emit_id,
        }
        return "signal_digest", data

    if ntype == NotificationType.ROUTINE_ELEVATION:
        data = {
            "connector": meta.get("connector"),
            "action_type": meta.get("action_type"),
            "approval_count": meta.get("approval_count"),
            "emit_id": emit_id,
        }
        return "routine_elevation_ready", data

    if ntype == NotificationType.SECURITY_ALERT:
        data = {
            "emit_id": emit_id,
            "notification_type": ntype.value,
            "tier": (notification.tier.value if notification.tier else None),
            "title": notification.title,
            "body": notification.body,
            "alert_type": meta.get("alert_type"),
            "resource": meta.get("resource"),
            "url": meta.get("url"),
            "severity": meta.get("severity"),
        }
        return "notification", data

    if ntype == NotificationType.ACTION_EXECUTED:
        data = {
            "emit_id": emit_id,
            "notification_type": ntype.value,
            "tier": (notification.tier.value if notification.tier else None),
            "title": notification.title,
            "body": notification.body,
            "action_name": meta.get("action_name"),
            "connector": meta.get("connector"),
            "success": meta.get("success"),
            "audit_id": meta.get("audit_id"),
        }
        return "notification", data

    if ntype == NotificationType.CONSOLIDATION_DONE:
        data = {
            "emit_id": emit_id,
            "notification_type": ntype.value,
            "tier": (notification.tier.value if notification.tier else None),
            "title": notification.title,
            "body": notification.body,
            "job_id": meta.get("job_id"),
            "summary": meta.get("summary", notification.body),
            "entity_count": meta.get("entity_count"),
        }
        return "notification", data

    data = {
        "emit_id": emit_id,
        "notification_type": ntype.value,
        "tier": (notification.tier.value if notification.tier else None),
        "title": notification.title,
        "body": notification.body,
    }
    return "notification", data


class InAppChannel:
    channel_id = ChannelId.IN_APP

    def deliver(self, notification: TypedNotification) -> ChannelDeliveryResult:
        event_type, data = _sse_payload(notification)
        events_routes.enqueue_user_sse(
            event_type,
            data,
            user_id=notification.user_id,
        )
        return ChannelDeliveryResult(channel=ChannelId.IN_APP, status="delivered")
