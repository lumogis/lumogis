# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unified notification layer (ADR 077 / LUM-93)."""

from services.notifications.dispatcher import emit
from services.notifications.dispatcher import register_hook_listeners
from services.notifications.dispatcher import resolve_effective_channels

__all__ = ["emit", "register_hook_listeners", "resolve_effective_channels"]
