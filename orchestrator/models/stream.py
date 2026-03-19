# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""Stream event types for the ask_stream() generator."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class StreamEvent:
    type: Literal["text", "tool_status", "error"]
    content: str
