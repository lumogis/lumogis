# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Notification taxonomy, dispatch DTOs, and preference API models (ADR 077 / LUM-93)."""

from __future__ import annotations

from enum import Enum
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

_REQ = ConfigDict(extra="forbid", str_strip_whitespace=True)
_RES = ConfigDict(extra="ignore")


class NotificationType(str, Enum):
    ROUTINE_ELEVATION = "routine_elevation"
    SIGNAL_RECEIVED = "signal_received"
    SIGNAL_DIGEST = "signal_digest"
    ACTION_EXECUTED = "action_executed"
    SECURITY_ALERT = "security_alert"
    CONSOLIDATION_DONE = "consolidation_done"


class NotificationTier(str, Enum):
    URGENT = "urgent"
    ACTION_REQUIRED = "action_required"
    INFORMATIONAL = "informational"
    BACKGROUND = "background"


class ChannelId(str, Enum):
    NTFY = "ntfy"
    WEB_PUSH = "web_push"
    IN_APP = "in_app"


# Types with no v1 producer — matrix still editable for forward compatibility.
PRODUCERLESS_NOTIFICATION_TYPES: frozenset[NotificationType] = frozenset(
    {
        NotificationType.SECURITY_ALERT,
        NotificationType.CONSOLIDATION_DONE,
    }
)


class TypedNotification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emit_id: str = ""
    user_id: str
    notification_type: NotificationType
    tier: NotificationTier | None = None
    title: str
    body: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChannelDeliveryResult(BaseModel):
    model_config = _RES

    channel: ChannelId
    status: Literal["delivered", "skipped", "failed"]
    reason: str | None = None


class DispatchResult(BaseModel):
    model_config = _RES

    emit_id: str
    user_id: str
    notification_type: NotificationType
    tier: NotificationTier
    channels: list[ChannelDeliveryResult]
    outcome: Literal["delivered", "partial", "all_skipped", "failed"]


class NotificationPreferenceCell(BaseModel):
    model_config = _RES

    channel: ChannelId
    enabled: bool
    effective: bool
    mutable: bool
    tier_default: bool


class NotificationTypePrefsRow(BaseModel):
    model_config = _RES

    notification_type: NotificationType
    tier: NotificationTier
    channels: list[NotificationPreferenceCell]


class NotificationPreferencesResponse(BaseModel):
    model_config = _RES

    types: list[NotificationTypePrefsRow]
    timezone: str | None = None
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None


class NotificationPreferencePatchItem(BaseModel):
    model_config = _REQ

    notification_type: NotificationType
    channel: ChannelId
    enabled: bool


class NotificationPreferencesPatch(BaseModel):
    model_config = _REQ

    preferences: list[NotificationPreferencePatchItem] = Field(min_length=1, max_length=50)


class NotificationTierPolicyRow(BaseModel):
    model_config = _RES

    tier: NotificationTier
    bypass_quiet_hours: bool
    default_channels: list[ChannelId]


class NotificationTierPolicyPatch(BaseModel):
    model_config = _REQ

    bypass_quiet_hours: bool | None = None
    default_channels: list[ChannelId] | None = None
