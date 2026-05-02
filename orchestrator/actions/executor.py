# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Action executor — the single entry point for running registered actions.

execute(name, input, user_id):
  1. Looks up ActionSpec from registry.
  2. Enforces hard limits (certain action_types can NEVER be elevated to routine Do).
  3. Calls permissions.check_permission().
  4. Calls spec.handler(input) and captures ActionResult.
  5. Writes AuditEntry to audit_log.
  6. Fires Event.ACTION_EXECUTED hook.
  7. Calls permissions.routine_check() so routine elevation can be evaluated.
  8. Returns ActionResult.

Hard-limited action_types (can NEVER be auto-elevated, regardless of approval count):
  financial_transaction, mass_communication, permanent_deletion,
  first_contact, code_commit

Permission bypass is not possible — every call goes through check_permission().
"""

import logging
import uuid
from datetime import datetime
from datetime import timezone
from typing import Any

import hooks
from actions.audit import write_audit
from actions.registry import get_action
from events import Event
from models.actions import ActionResult
from models.actions import AuditEntry

_log = logging.getLogger(__name__)

_HARD_LIMITED = frozenset(
    {
        "financial_transaction",
        "mass_communication",
        "permanent_deletion",
        "first_contact",
        "code_commit",
    }
)


def execute(
    name: str,
    input: dict[str, Any] | None = None,
    *,
    user_id: str,
) -> ActionResult:
    """Execute a registered action by name. Never bypasses permission check.

    Phase 3: ``user_id`` is keyword-only and required. Every audit row,
    permission log entry, and ACTION_EXECUTED hook payload carries the
    caller's identity end-to-end. Callers that forget the kwarg fail
    loud at import-call time with :class:`TypeError`.
    """
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("actions.executor.execute: user_id (keyword-only) is required")
    input = input or {}

    spec = get_action(name)
    if spec is None:
        return ActionResult(success=False, output="", error=f"Unknown action: {name!r}")

    # Permission check via permissions module — user_id propagates so
    # action_log captures who attempted what, even on denials.
    from permissions import check_permission
    from permissions import routine_check

    allowed = check_permission(spec.connector, spec.action_type, spec.is_write, user_id=user_id)
    if not allowed:
        result = ActionResult(
            success=False,
            output="",
            error=f"Permission denied: {spec.connector}/{spec.action_type} requires DO mode",
        )
        _write_audit_and_fire(spec, input, result, user_id)
        return result

    # Execute handler.
    try:
        result = spec.handler(input)
        if not isinstance(result, ActionResult):
            result = ActionResult(success=True, output=str(result))
    except Exception as exc:
        _log.error("Action %r handler raised: %s", name, exc)
        result = ActionResult(success=False, output="", error=str(exc))

    # Assign reverse token if reversible and successful.
    if result.success and spec.is_reversible and result.reverse_token is None:
        result.reverse_token = str(uuid.uuid4())

    _write_audit_and_fire(spec, input, result, user_id)

    # Routine elevation check — only for successful DO-mode writes.
    if result.success and spec.is_write and spec.action_type not in _HARD_LIMITED:
        try:
            routine_check(spec.connector, spec.action_type, user_id=user_id)
        except Exception as exc:
            _log.debug("routine_check error: %s", exc)

    return result


def is_hard_limited(action_type: str) -> bool:
    """Return True if action_type can never be elevated to routine Do."""
    return action_type in _HARD_LIMITED


def _write_audit_and_fire(spec, input: dict, result: ActionResult, user_id: str) -> None:
    """Write audit entry and fire ACTION_EXECUTED hook."""
    import json

    entry = AuditEntry(
        action_name=spec.name,
        connector=spec.connector,
        mode="DO" if spec.is_write else "ASK",
        input_summary=json.dumps(input, default=str)[:500],
        result_summary=(result.output or result.error or "")[:500],
        reverse_action=(
            {"action_name": spec.reverse_action_name, "reverse_token": result.reverse_token}
            if spec.is_reversible and result.reverse_token
            else None
        ),
        executed_at=datetime.now(timezone.utc),
        user_id=user_id,
    )
    audit_id = write_audit(entry, reverse_token=result.reverse_token)

    hooks.fire_background(
        Event.ACTION_EXECUTED,
        action_name=spec.name,
        connector=spec.connector,
        success=result.success,
        reverse_token=result.reverse_token,
        audit_id=audit_id,
        user_id=user_id,
    )
