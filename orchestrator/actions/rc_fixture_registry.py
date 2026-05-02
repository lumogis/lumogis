# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Opt-in registry rows for RC approvals UI / integration tests.

When ``LUMOGIS_RC_APPROVAL_FIXTURE_ACTIONS`` is truthy, registers a single
read-only :class:`ActionSpec` on connector ``filesystem-mcp`` so
``POST /api/v1/approvals/connector/{connector}/mode`` can resolve the
connector via :func:`actions.registry.list_actions`.

This is **test/RC compose only** — never enable on production stacks.
"""

from __future__ import annotations

import logging
import os

from actions.registry import register_action
from models.actions import ActionResult
from models.actions import ActionSpec

_log = logging.getLogger(__name__)

_registered = False


def register_rc_approval_fixture_actions_if_enabled() -> None:
    """No-op unless ``LUMOGIS_RC_APPROVAL_FIXTURE_ACTIONS`` is enabled."""
    global _registered
    raw = os.environ.get("LUMOGIS_RC_APPROVAL_FIXTURE_ACTIONS", "").strip().lower()
    if raw not in ("1", "true", "yes"):
        return
    if _registered:
        return

    def _handler(_input: dict) -> ActionResult:
        return ActionResult(success=True, output="rc_fixture_noop")

    register_action(
        ActionSpec(
            name="__rc_fixture_filesystem_probe",
            connector="filesystem-mcp",
            action_type="integration_probe_read",
            is_write=False,
            is_reversible=False,
            handler=_handler,
        )
    )
    _registered = True
    _log.info("RC fixture actions registered (filesystem-mcp / integration_probe_read)")
