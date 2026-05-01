# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Port: action handler protocol.

Implemented by concrete action handlers in actions/handlers/.
Core code never imports from the handlers directory directly — it
calls them via actions/executor.py which dispatches through ActionSpec.handler.
"""

from typing import Any
from typing import Protocol
from typing import runtime_checkable

from models.actions import ActionResult


@runtime_checkable
class ActionHandler(Protocol):
    def execute(self, spec, input: dict[str, Any]) -> ActionResult:
        """Execute the action. Returns ActionResult with success/output/error."""
        ...

    def can_reverse(self, reverse_token: str) -> bool:
        """Return True if this reverse_token can be undone by this handler."""
        ...
