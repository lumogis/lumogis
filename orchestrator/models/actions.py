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


@dataclass
class ActionResult:
    success: bool
    output: str
    error: Optional[str] = None
    # UUID token for reversibility — None if action is not reversible.
    # Pass to POST /audit/{reverse_token}/reverse to undo.
    reverse_token: Optional[str] = None


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
    steps: list[dict] = field(default_factory=list)  # [{action_name, input}]
    requires_approval: bool = True
    approved_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    enabled: bool = True
    user_id: str = "default"
