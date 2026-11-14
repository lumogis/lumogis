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

import inspect
import logging
import uuid
from datetime import datetime
from datetime import timezone
from typing import Any

import hooks
from actions.audit import write_audit
from actions.errors import PermanentActionError
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
    approved: bool = False,
    annotation: dict[str, Any] | None = None,
    proposal_id: int | None = None,
) -> ActionResult:
    """Execute a registered action by name. Never bypasses permission check.

    Phase 3: ``user_id`` is keyword-only and required. Every audit row,
    permission log entry, and ACTION_EXECUTED hook payload carries the
    caller's identity end-to-end. Callers that forget the kwarg fail
    loud at import-call time with :class:`TypeError`.

    ``approved`` (LUM-623): when True, an explicit per-message approval has
    already occurred (the caller claimed a human-approved proposal row, or is
    reversing their own audit entry), so an ASK-mode reversible write is
    permitted. It never relaxes a hard-limited action. Direct callers
    (routine steps) leave it False and keep today's ASK-blocks-writes behaviour.

    ``annotation`` (LUM-650): the proposal's stored risk annotation, threaded in
    on the claimed-proposal execute path. A truthy ``first_contact`` forces Ask
    (a per-message approval required even in DO mode). On a DIRECT execute path
    (no proposal, ``annotation is None``) that is NOT already an explicit
    approval, the action's ``annotate`` hook is recomputed **fail-closed** so a
    first-contact send can never slip through the DO-mode bypass.

    ``proposal_id`` (LUM-793): the id of the claimed ``action_proposals`` row, when
    this execution came from one. Threaded the same way ``user_id`` was in LUM-623 —
    signature introspection, passed only to handlers that declare it — so direct
    callers (``routines.py``, reverse paths) are unaffected by the default ``None``.
    The send handlers write it to ``email_sent_log`` so a LATER recovery surface can
    tell whether a stranded proposal's message actually went out. It has to travel
    this way: the id does not exist when the payload is built (it comes from
    ``create_proposal``'s ``INSERT … RETURNING``), ``annotation`` is the LUM-650 risk
    annotation and must not be overloaded, and the caller cannot record the link
    afterwards because the whole point is surviving a proposal that never got that far.
    """
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("actions.executor.execute: user_id (keyword-only) is required")
    input = input or {}

    spec = get_action(name)
    if spec is None:
        return ActionResult(success=False, output="", error=f"Unknown action: {name!r}")

    force_ask = _resolve_force_ask(spec, input, user_id, approved=approved, annotation=annotation)

    # Permission check via permissions module — user_id propagates so
    # action_log captures who attempted what, even on denials.
    from permissions import check_permission
    from permissions import routine_check

    allowed = check_permission(
        spec.connector,
        spec.action_type,
        spec.is_write,
        user_id=user_id,
        approved=approved,
        force_ask=force_ask,
    )
    if not allowed:
        result = ActionResult(
            success=False,
            output="",
            error=f"Permission denied: {spec.connector}/{spec.action_type} requires DO mode",
        )
        _write_audit_and_fire(spec, input, result, user_id)
        return result

    # Execute handler. Pass user_id when the handler's signature accepts it
    # (LUM-623), else use the legacy input-only call. The decision is made by
    # inspecting the signature ONCE up front — never by catch-and-retry, which
    # could re-run a mutating handler or mask a genuine internal TypeError.
    try:
        handler_kwargs: dict[str, Any] = {}
        if _handler_accepts_user_id(spec.handler):
            handler_kwargs["user_id"] = user_id
        if _handler_accepts_proposal_id(spec.handler):
            handler_kwargs["proposal_id"] = proposal_id
        raw = spec.handler(input, **handler_kwargs) if handler_kwargs else spec.handler(input)
        result = (
            raw if isinstance(raw, ActionResult) else ActionResult(success=True, output=str(raw))
        )
    except PermanentActionError as exc:
        _log.warning("Action %r handler permanent failure: %s", name, exc)
        result = ActionResult(
            success=False, output="", error=str(exc), permanent=True, code=exc.code
        )
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


def _resolve_force_ask(
    spec,
    input: dict,
    user_id: str,
    *,
    approved: bool,
    annotation: dict[str, Any] | None,
) -> bool:
    """Decide whether this call must be forced to Ask (LUM-650 first-contact gate).

    * A write is never force-asked unless it declares an ``annotate`` hook.
    * When a stored ``annotation`` is supplied (the claimed-proposal execute
      path), read it — never recompute — so the gate matches what the user was
      shown at approval.
    * When no annotation is supplied AND the call is not an explicit approval
      (the direct DO-mode execute path — the one a future autonomous sender or a
      Do-mode agent would hit), recompute the annotation from the hook,
      **failing closed** (any error → force Ask). An approved call skips the
      recompute (the human already approved).
    """
    if not spec.is_write or getattr(spec, "annotate", None) is None:
        return False
    ann = annotation
    if ann is None and not approved:
        try:
            ann = spec.annotate(input, user_id)
        except Exception:  # noqa: BLE001 - fail closed
            return True
    if not isinstance(ann, dict):
        return False
    # `first_contact` is LUM-650's key and stays the one the Approvals banner
    # reads. `force_ask` (LUM-729) is the general form, added when `imap_move`
    # needed the same gate for a reason that is not first contact at all: a
    # Do-mode move can take a message out of INBOX before paperless consumes it.
    # Reusing `first_contact` there would have been a lie in the field name —
    # LUM-697 could honestly claim it ("an unsubscribe address IS one you have
    # never emailed"); a folder move cannot.
    return ann.get("first_contact") is True or ann.get("force_ask") is True


def is_hard_limited(action_type: str) -> bool:
    """Return True if action_type can never be elevated to routine Do."""
    return action_type in _HARD_LIMITED


def _handler_accepts_proposal_id(handler) -> bool:
    """True if ``handler`` declares a ``proposal_id`` parameter (LUM-793).

    Mirrors :func:`_handler_accepts_user_id` deliberately, including its posture on
    un-introspectable handlers (treated as legacy, not passed the kwarg). It does
    **not** treat ``**kwargs`` as acceptance, unlike the ``user_id`` variant: a
    handler that swallows unknown kwargs would silently receive a value it never
    asked for and never uses, and the whole point of this thread is that exactly
    three handlers do something with it.
    """
    try:
        params = inspect.signature(handler).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == "proposal_id" for p in params)


def _handler_accepts_user_id(handler) -> bool:
    """True if ``handler`` can receive the executor-supplied ``user_id`` kwarg —
    either it declares a ``user_id`` parameter or accepts ``**kwargs``. Used to
    choose the call signature without a catch-and-retry (LUM-623). A handler
    whose signature cannot be introspected is treated as legacy (input-only).
    """
    try:
        params = inspect.signature(handler).parameters.values()
    except (TypeError, ValueError):
        return False
    for p in params:
        if p.name == "user_id" or p.kind is inspect.Parameter.VAR_KEYWORD:
            return True
    return False


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
            {
                "action_name": spec.reverse_action_name,
                "reverse_token": result.reverse_token,
                # Persist the reverse handler input so attempt_reverse can run
                # the inverse with real arguments (LUM-623). {} when the handler
                # supplied none (e.g. a self-inverse toggle).
                "input": result.reverse_input or {},
            }
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
