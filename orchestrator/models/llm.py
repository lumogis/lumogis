# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""Canonical LLM response types shared across all providers."""

from dataclasses import dataclass, field


@dataclass
class LLMToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[LLMToolCall] = field(default_factory=list)
    stop_reason: str = "stop"  # "stop" | "tool_calls"


@dataclass
class LLMEvent:
    type: str  # "text" | "tool_call" | "end"
    content: str = ""
    tool_call: LLMToolCall | None = None
    stop_reason: str | None = None
