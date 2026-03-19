# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""ToolSpec: mandatory metadata for every tool (core and plugin).

Permission enforcement in run_tool() reads these fields structurally;
there is no way to register a tool without declaring its safety metadata.
"""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ToolSpec:
    name: str
    connector: str
    action_type: str
    is_write: bool
    definition: dict
    handler: Callable
