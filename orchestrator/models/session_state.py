# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Frozen session state types for the orchestrator chat tool loop (LUM-128)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass
class ToolChainBudget:
    """Per-request pessimistic dispatch counter for abusive tool-loop fan-out."""

    cap: int
    observed: int = 0
    tripped_event: bool = False


class TransitionReason(Enum):
    MODEL_CALL = "model_call"
    TOOL_DISPATCH = "tool_dispatch"
    TURN_ADVANCE = "turn_advance"
    TERMINATE = "terminate"


class SessionTerminal(Enum):
    COMPLETED = "completed"
    MAX_TOOL_ROUNDS = "max_tool_rounds"


class ContinueSite(Enum):
    PRE_REQUEST = "pre_request"
    MODEL_CALL = "model_call"
    TOOL_DISPATCH = "tool_dispatch"
    TURN_ADVANCE = "turn_advance"


class SessionLoopEvent(Enum):
    SITE_MODEL_CALL = "site_model_call"
    SITE_TOOL_DISPATCH = "site_tool_dispatch"
    SITE_TURN_ADVANCE = "site_turn_advance"
    SITE_TERMINATE = "site_terminate"


@dataclass(frozen=True)
class SessionParams:
    user_id: str
    system: str
    tools: list[dict] | None
    use_tools: bool
    model: str
    auto_rag_point_ids: set[str] | None


@dataclass(frozen=True)
class SessionState:
    messages: tuple[dict, ...]
    turn_count: int
    tool_chain_budget: ToolChainBudget | None
    transition: TransitionReason | None
    terminal: SessionTerminal | None


def initial_session_state(
    *,
    messages: list[dict] | tuple[dict, ...],
    chain_budget: ToolChainBudget | None,
) -> SessionState:
    return SessionState(
        messages=tuple(messages),
        turn_count=0,
        tool_chain_budget=chain_budget,
        transition=None,
        terminal=None,
    )
