# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""
Tool-calling loop for the Lumogis orchestrator.

Provider-agnostic: uses the LLMProvider protocol from config.get_llm_provider().
Tool capability and model selection are driven by config/models.yaml.

Exports:
    ask()        — synchronous, returns final text
    ask_stream() — generator, yields StreamEvent objects for real-time streaming
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Generator

import hooks
from events import Event
from models.llm import LLMResponse
from models.session_state import SessionLoopEvent
from models.session_state import SessionParams
from models.session_state import SessionState
from models.session_state import SessionTerminal
from models.session_state import ToolChainBudget  # noqa: F401 — re-export for test compat
from models.session_state import TransitionReason
from models.session_state import initial_session_state
from models.stream import StreamEvent
from services.tools import TOOLS
from services.tools import run_tool
from services.unified_tools import finish_llm_tools_request
from services.unified_tools import prepare_llm_tools_for_request

import config

_log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 2

SYSTEM_PROMPT_TOOLS = (
    "You are Lumogis, a local-first AI assistant. "
    "You have access to tools that search and read the user's local files. "
    "IMPORTANT: Be efficient with tool calls. Search once with a broad query, "
    "then answer using the results you get. Do NOT search repeatedly with "
    "slight variations. If the first search returns relevant results, "
    "use read_file on the most promising one and then give your answer. "
    "Aim to answer within 1-2 tool calls."
)

SYSTEM_PROMPT_NO_TOOLS = (
    "You are Lumogis, a local-first AI assistant running locally on the user's machine. "
    "You do NOT have access to any tools, file search, or file reading capabilities. "
    "Never pretend to search files, read files, or call tools. Never fabricate file names "
    "or file contents. If the user asks you to search or read their files, tell them to "
    "switch to Claude (Cloud) or Qwen 2.5 (Local) which have file search capabilities. "
    "Answer questions using only your own knowledge."
)


def _lumogis_blocked_payload(tool_name: str, budget: ToolChainBudget) -> str:
    return json.dumps(
        {
            "lumogis_blocked": True,
            "reason": "tool_chain_cap",
            "blocked_tool": tool_name,
            "cap": budget.cap,
            "observed": budget.observed,
        }
    )


def dispatch_tool_under_cap(
    tool_name: str,
    arguments: dict,
    *,
    user_id: str,
    budget: ToolChainBudget | None,
    auto_rag_point_ids: set[str] | None = None,
) -> str:
    """Increment pessimistic budget before ``run_tool``; return stub JSON when tripped."""

    if budget is None or budget.cap <= 0:
        return run_tool(
            tool_name,
            arguments,
            user_id=user_id,
            auto_rag_point_ids=auto_rag_point_ids,
        )

    if budget.observed >= budget.cap:
        if not budget.tripped_event:
            budget.tripped_event = True
            hooks.fire_background(
                Event.TOOL_CHAIN_CAP_TRIPPED,
                user_id=user_id,
                cap=budget.cap,
                observed=budget.observed,
            )
        return _lumogis_blocked_payload(tool_name, budget)

    budget.observed += 1
    return run_tool(tool_name, arguments, user_id=user_id, auto_rag_point_ids=auto_rag_point_ids)


def _system_prompt(use_tools: bool) -> str:
    return SYSTEM_PROMPT_TOOLS if use_tools else SYSTEM_PROMPT_NO_TOOLS


def _fire_loop_event(
    on_loop_event: Callable[[SessionLoopEvent, SessionState], None] | None,
    event: SessionLoopEvent,
    state: SessionState,
) -> None:
    if on_loop_event is not None:
        on_loop_event(event, state)


def _finish_session_loop(
    gen: Generator[StreamEvent, None, tuple[SessionTerminal, SessionState]],
) -> tuple[SessionTerminal, SessionState]:
    try:
        while True:
            next(gen)
    except StopIteration as exc:
        return exc.value


def _assistant_from_llm_response(response: LLMResponse) -> dict:
    assistant_msg: dict = {"role": "assistant", "content": response.text}
    if response.tool_calls:
        assistant_msg["tool_calls"] = [
            {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
            for tc in response.tool_calls
        ]
    return assistant_msg


def _sync_should_dispatch(response: LLMResponse) -> bool:
    return response.stop_reason == "tool_calls" and bool(response.tool_calls)


def _run_session_loop(
    state: SessionState,
    params: SessionParams,
    *,
    provider,
    stream: bool,
    on_loop_event: Callable[[SessionLoopEvent, SessionState], None] | None = None,
) -> Generator[StreamEvent, None, tuple[SessionTerminal, SessionState]]:
    """Shared sync/stream tool loop with atomic SessionState transitions."""

    for _round_index in range(MAX_TOOL_ROUNDS + 1):
        messages_for_provider = list(state.messages)

        if not stream:
            response: LLMResponse = provider.chat(
                messages_for_provider,
                tools=params.tools,
                system=params.system,
                max_tokens=4096,
            )
            assistant_msg = _assistant_from_llm_response(response)
            state = replace(state, transition=TransitionReason.MODEL_CALL)
            _fire_loop_event(on_loop_event, SessionLoopEvent.SITE_MODEL_CALL, state)

            if not _sync_should_dispatch(response):
                state = replace(
                    state,
                    messages=state.messages + (assistant_msg,),
                    transition=TransitionReason.TERMINATE,
                    terminal=SessionTerminal.COMPLETED,
                )
                _fire_loop_event(on_loop_event, SessionLoopEvent.SITE_TERMINATE, state)
                return SessionTerminal.COMPLETED, state

            tool_messages: list[dict] = []
            for tc in response.tool_calls:
                result = dispatch_tool_under_cap(
                    tc.name,
                    tc.arguments,
                    user_id=params.user_id,
                    budget=state.tool_chain_budget,
                    auto_rag_point_ids=params.auto_rag_point_ids,
                )
                tool_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )

            state = replace(
                state,
                messages=state.messages + (assistant_msg,) + tuple(tool_messages),
                transition=TransitionReason.TOOL_DISPATCH,
            )
            _fire_loop_event(on_loop_event, SessionLoopEvent.SITE_TOOL_DISPATCH, state)

            state = replace(
                state,
                turn_count=state.turn_count + 1,
                transition=TransitionReason.TURN_ADVANCE,
            )
            _fire_loop_event(on_loop_event, SessionLoopEvent.SITE_TURN_ADVANCE, state)
            continue

        text_parts: list[str] = []
        tool_calls: list[dict] = []

        for event in provider.chat_stream(
            messages_for_provider,
            tools=params.tools,
            system=params.system,
            max_tokens=4096,
        ):
            if event.type == "text":
                text_parts.append(event.content)
                yield StreamEvent(type="text", content=event.content)
            elif event.type == "tool_call" and event.tool_call:
                tool_calls.append(
                    {
                        "id": event.tool_call.id,
                        "name": event.tool_call.name,
                        "arguments": event.tool_call.arguments,
                    }
                )
            elif event.type == "end":
                break

        assistant_msg = {"role": "assistant", "content": "".join(text_parts)}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls

        state = replace(state, transition=TransitionReason.MODEL_CALL)
        _fire_loop_event(on_loop_event, SessionLoopEvent.SITE_MODEL_CALL, state)

        if not tool_calls:
            state = replace(
                state,
                messages=state.messages + (assistant_msg,),
                transition=TransitionReason.TERMINATE,
                terminal=SessionTerminal.COMPLETED,
            )
            _fire_loop_event(on_loop_event, SessionLoopEvent.SITE_TERMINATE, state)
            return SessionTerminal.COMPLETED, state

        tool_messages = []
        for tc in tool_calls:
            result = dispatch_tool_under_cap(
                tc["name"],
                tc["arguments"],
                user_id=params.user_id,
                budget=state.tool_chain_budget,
                auto_rag_point_ids=params.auto_rag_point_ids,
            )
            tool_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                }
            )

        state = replace(
            state,
            messages=state.messages + (assistant_msg,) + tuple(tool_messages),
            transition=TransitionReason.TOOL_DISPATCH,
        )
        _fire_loop_event(on_loop_event, SessionLoopEvent.SITE_TOOL_DISPATCH, state)

        state = replace(
            state,
            turn_count=state.turn_count + 1,
            transition=TransitionReason.TURN_ADVANCE,
        )
        _fire_loop_event(on_loop_event, SessionLoopEvent.SITE_TURN_ADVANCE, state)
        yield StreamEvent(type="text", content="\n\n")

    _log.warning("Tool loop hit MAX_TOOL_ROUNDS=%d, forcing final answer", MAX_TOOL_ROUNDS)
    messages_for_provider = list(state.messages)

    if not stream:
        final = provider.chat(messages_for_provider, system=params.system, max_tokens=4096)
        assistant_msg = _assistant_from_llm_response(final)
        state = replace(state, transition=TransitionReason.MODEL_CALL)
        _fire_loop_event(on_loop_event, SessionLoopEvent.SITE_MODEL_CALL, state)
        state = replace(
            state,
            messages=state.messages + (assistant_msg,),
            transition=TransitionReason.TERMINATE,
            terminal=SessionTerminal.MAX_TOOL_ROUNDS,
        )
        _fire_loop_event(on_loop_event, SessionLoopEvent.SITE_TERMINATE, state)
        return SessionTerminal.MAX_TOOL_ROUNDS, state

    text_parts = []
    for event in provider.chat_stream(
        messages_for_provider,
        system=params.system,
        max_tokens=4096,
    ):
        if event.type == "text":
            text_parts.append(event.content)
            yield StreamEvent(type="text", content=event.content)
        elif event.type == "end":
            break

    assistant_msg = {"role": "assistant", "content": "".join(text_parts)}
    state = replace(state, transition=TransitionReason.MODEL_CALL)
    _fire_loop_event(on_loop_event, SessionLoopEvent.SITE_MODEL_CALL, state)
    state = replace(
        state,
        messages=state.messages + (assistant_msg,),
        transition=TransitionReason.TERMINATE,
        terminal=SessionTerminal.MAX_TOOL_ROUNDS,
    )
    _fire_loop_event(on_loop_event, SessionLoopEvent.SITE_TERMINATE, state)
    return SessionTerminal.MAX_TOOL_ROUNDS, state


def _build_session_params_and_state(
    *,
    question: str,
    history: list | None,
    model: str,
    use_tools: bool,
    user_id: str,
    tools: list[dict] | None,
    system: str,
    chain_budget: ToolChainBudget | None,
    auto_rag_point_ids: set[str] | None,
) -> tuple[SessionParams, SessionState]:
    messages = list(history) if history else []
    messages.append({"role": "user", "content": question})
    params = SessionParams(
        user_id=user_id,
        system=system,
        tools=tools,
        use_tools=use_tools,
        model=model,
        auto_rag_point_ids=auto_rag_point_ids,
    )
    state = initial_session_state(messages=messages, chain_budget=chain_budget)
    return params, state


def ask(
    question: str,
    history: list | None = None,
    model: str = "claude",
    use_tools: bool = True,
    *,
    user_id: str,
    auto_rag_point_ids: set[str] | None = None,
) -> str:
    """Synchronous tool-loop. ``user_id`` is keyword-only and required."""
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("loop.ask: user_id (keyword-only) is required")

    provider = config.get_llm_provider(model, user_id=user_id)

    oop_tok = None
    if use_tools:
        try:
            tools, oop_tok = prepare_llm_tools_for_request(user_id)
        except Exception:  # noqa: BLE001 — fail closed to unextended TOOLS
            _log.warning("prepare_llm_tools_for_request failed; using default TOOLS", exc_info=True)
            tools, oop_tok = TOOLS, None
    else:
        tools = None
    system = _system_prompt(use_tools)

    cap_cfg = config.get_tool_chain_cap()
    chain_budget = ToolChainBudget(cap=cap_cfg) if cap_cfg > 0 else None

    params, state = _build_session_params_and_state(
        question=question,
        history=history,
        model=model,
        use_tools=use_tools,
        user_id=user_id,
        tools=tools,
        system=system,
        chain_budget=chain_budget,
        auto_rag_point_ids=auto_rag_point_ids,
    )

    try:
        _terminal, final_state = _finish_session_loop(
            _run_session_loop(state, params, provider=provider, stream=False)
        )
        last = final_state.messages[-1]
        content = last.get("content", "")
        return content if isinstance(content, str) else str(content)
    finally:
        if oop_tok is not None:
            finish_llm_tools_request(oop_tok)


def ask_stream(
    question: str,
    history: list | None = None,
    model: str = "claude",
    use_tools: bool = True,
    *,
    user_id: str,
    auto_rag_point_ids: set[str] | None = None,
) -> Generator[StreamEvent, None, None]:
    """Stream responses token-by-token. ``user_id`` is keyword-only and required."""
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("loop.ask_stream: user_id (keyword-only) is required")

    oop_tok = None
    if use_tools:
        try:
            tools, oop_tok = prepare_llm_tools_for_request(user_id)
        except Exception:  # noqa: BLE001 — fail closed to unextended TOOLS
            _log.warning(
                "prepare_llm_tools_for_request failed; using default TOOLS (stream)",
                exc_info=True,
            )
            tools, oop_tok = TOOLS, None
    else:
        tools = None
    system = _system_prompt(use_tools)

    cap_cfg = config.get_tool_chain_cap()
    chain_budget = ToolChainBudget(cap=cap_cfg) if cap_cfg > 0 else None

    try:
        try:
            provider = config.get_llm_provider(model, user_id=user_id)
            params, state = _build_session_params_and_state(
                question=question,
                history=history,
                model=model,
                use_tools=use_tools,
                user_id=user_id,
                tools=tools,
                system=system,
                chain_budget=chain_budget,
                auto_rag_point_ids=auto_rag_point_ids,
            )
            yield from _run_session_loop(
                state, params, provider=provider, stream=True
            )
        except Exception as exc:
            _log.exception("ask_stream failed for model=%s", model)
            yield StreamEvent(type="error", content=_friendly_error(exc))
    finally:
        if oop_tok is not None:
            finish_llm_tools_request(oop_tok)


def _friendly_error(exc: Exception) -> str:
    """Turn raw API exceptions into short, user-facing messages."""
    msg = str(exc).lower()
    if "rate_limit" in msg or "429" in msg:
        return "The AI provider's rate limit was reached. Please wait a minute and try again."
    if "401" in msg or "auth" in msg:
        return "Authentication failed. Check your API key in .env."
    if "timeout" in msg:
        return "The request timed out. Please try again."
    return "Sorry, something went wrong. Check the orchestrator logs for details."
