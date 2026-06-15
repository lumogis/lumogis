# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Port: notification channel adapter (distinct from :class:`ports.notifier.Notifier`)."""

from __future__ import annotations

from typing import Protocol
from typing import runtime_checkable

from models.notifications import ChannelDeliveryResult
from models.notifications import ChannelId
from models.notifications import TypedNotification


@runtime_checkable
class NotificationChannel(Protocol):
    channel_id: ChannelId

    def deliver(self, notification: TypedNotification) -> ChannelDeliveryResult:
        """Return delivered/skipped/failed; do not raise for expected skips."""
        ...
