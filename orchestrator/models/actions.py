# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Actions foundation models.

ActionSpec    — registered action descriptor (name, connector, handler, etc.)
ActionResult  — return value from executor.execute()
AuditEntry    — what gets written to audit_log on every execution
RoutineSpec   — scheduled multi-step workflow descriptor
"""

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from typing import Any
from typing import Callable
from typing import Optional


@dataclass
class ActionSpec:
    name: str
    connector: str
    action_type: str
    is_write: bool
    is_reversible: bool
    handler: Callable[..., Any]
    definition: dict = field(default_factory=dict)
    reverse_action_name: Optional[str] = None
    # Optional per-action payload model. When set, the connector-agnostic
    # proposal producer validates a create request's ``payload`` against it
    # (``extra="forbid"`` rejects smuggled fields) — so the producer stays
    # generic while each action declares its own input contract (LUM-623).
    input_model: Optional[type] = None
    # Optional create-time risk-annotation hook (LUM-650). ``(payload, user_id)
    # -> annotation dict``. The generic proposal producer calls it at
    # ``create_proposal`` and persists the result on the proposal's
    # ``annotation`` column, so an approval-relevant fact (e.g. ``first_contact``)
    # is available BEFORE the Ask/Do decision — the producer stays connector-
    # agnostic while an action supplies the fact. A general mechanism, not
    # email-only; the hook and column must FAIL CLOSED on any error (the producer
    # stores a safe-by-omission default when the hook raises).
    annotate: Optional[Callable[[dict, str], dict]] = None


@dataclass
class ActionResult:
    success: bool
    output: str
    error: Optional[str] = None
    # UUID token for reversibility — None if action is not reversible.
    # Pass to POST /audit/{reverse_token}/reverse to undo.
    reverse_token: Optional[str] = None
    # Reverse handler input, persisted into the audit reverse_action so
    # attempt_reverse can run the inverse with real arguments (LUM-623).
    reverse_input: Optional[dict] = None
    # True when the failure is permanent (retrying cannot help) — the proposal
    # queue dead-letters on the first failure instead of re-approving (LUM-623).
    permanent: bool = False
    # Short machine token for a failure (e.g. "no_drafts_folder", "egress_blocked",
    # "send_not_enabled") — copied from PermanentActionError.code by the executor so
    # a UI can branch on the failure kind without matching the human ``error`` string
    # (str(exc) drops the code). None on success or a non-coded failure (LUM-652).
    code: Optional[str] = None


@dataclass
class AuditEntry:
    action_name: str
    connector: str
    mode: str
    input_summary: str
    result_summary: str
    reverse_action: Optional[dict] = None
    executed_at: Optional[datetime] = None
    user_id: str = "default"


@dataclass
class RoutineSpec:
    name: str
    description: str
    schedule_cron: str  # APScheduler CronTrigger expression: "min hour dom mon dow"
    user_id: str
    steps: list[dict] = field(default_factory=list)  # [{action_name, input}]
    requires_approval: bool = True
    approved_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    enabled: bool = True
