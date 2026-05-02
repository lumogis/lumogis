# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Port: notifier protocol.

Implemented by ntfy_notifier and null_notifier.
"""

from typing import Protocol
from typing import runtime_checkable


@runtime_checkable
class Notifier(Protocol):
    def notify(
        self,
        title: str,
        message: str,
        priority: float,
        *,
        user_id: str,
    ) -> bool:
        """Send a notification. Returns True on success, False on failure.

        ``priority``: 0.0–1.0 importance_score passed from signal_processor.
        Implementations map this to their own priority scheme.

        ``user_id`` is keyword-only and required: per-user connector
        credentials (ADR 018) mean delivery config (topic, token, server
        URL) is resolved per recipient. Implementations that ignore the
        recipient (``NullNotifier``) still accept the parameter so the
        wire contract is uniform.
        """
        ...
