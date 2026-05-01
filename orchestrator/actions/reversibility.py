# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Reversibility — attempt to undo a previously executed action.

attempt_reverse(reverse_token):
  1. Looks up the original AuditEntry by reverse_token.
  2. Checks the entry hasn't already been reversed.
  3. Finds the reverse handler via reverse_action["action_name"] in the registry.
  4. Calls executor.execute() with the reverse action.
  5. Marks the original entry as reversed (sets reversed_at).
  6. Returns ActionResult from the reversal.
"""

import logging

from actions.audit import mark_reversed
from models.actions import ActionResult

import config

_log = logging.getLogger(__name__)


def attempt_reverse(reverse_token: str, user_id: str = "default") -> ActionResult:
    """Attempt to reverse the action identified by reverse_token."""
    # Look up original audit entry.
    try:
        ms = config.get_metadata_store()
        row = ms.fetch_one(
            "SELECT id, action_name, connector, reverse_action, reversed_at "
            "FROM audit_log WHERE reverse_token = %s AND user_id = %s",
            (reverse_token, user_id),
        )
    except Exception as exc:
        return ActionResult(success=False, output="", error=f"Audit lookup failed: {exc}")

    if not row:
        return ActionResult(
            success=False,
            output="",
            error=f"No reversible action found for token: {reverse_token}",
        )

    if row.get("reversed_at"):
        return ActionResult(
            success=False,
            output="",
            error=f"Action {row['action_name']!r} has already been reversed",
        )

    reverse_action = row.get("reverse_action")
    if not reverse_action:
        return ActionResult(
            success=False,
            output="",
            error=f"Action {row['action_name']!r} has no reverse action defined",
        )

    # Execute the reverse action.
    from actions.executor import execute

    reverse_name = reverse_action.get("action_name")
    reverse_input = reverse_action.get("input", {})

    if not reverse_name:
        return ActionResult(
            success=False,
            output="",
            error="Reverse action has no action_name",
        )

    _log.info("Reversing action %r via %r", row["action_name"], reverse_name)
    result = execute(reverse_name, reverse_input, user_id=user_id)

    if result.success:
        mark_reversed(reverse_token)
        _log.info("Action %r successfully reversed", row["action_name"])
    else:
        _log.warning("Reversal of %r failed: %s", row["action_name"], result.error)

    return result
