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

import logging
from typing import Generator

from models.llm import LLMResponse
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


def _system_prompt(use_tools: bool) -> str:
    return SYSTEM_PROMPT_TOOLS if use_tools else SYSTEM_PROMPT_NO_TOOLS


def ask(
    question: str,
    history: list | None = None,
    model: str = "claude",
    use_tools: bool = True,
    *,
    user_id: str,
) -> str:
    """Synchronous tool-loop. ``user_id`` is keyword-only and required.

    Phase 3: every chat path threads the caller's ``user_id`` down to
    :func:`services.tools.run_tool` so per-user data stores never leak
    across users. Callers that forget the kwarg fail loud at import-call
    time with :class:`TypeError`.
    """
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("loop.ask: user_id (keyword-only) is required")

    provider = config.get_llm_provider(model, user_id=user_id)
    messages = list(history) if history else []
    messages.append({"role": "user", "content": question})

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

    try:
        for _round in range(MAX_TOOL_ROUNDS + 1):
            response: LLMResponse = provider.chat(
                messages,
                tools=tools,
                system=system,
                max_tokens=4096,
            )

            assistant_msg: dict = {"role": "assistant", "content": response.text}
            if response.tool_calls:
                assistant_msg["tool_calls"] = [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in response.tool_calls
                ]
            messages.append(assistant_msg)

            if response.stop_reason != "tool_calls" or not response.tool_calls:
                return response.text

            for tc in response.tool_calls:
                result = run_tool(tc.name, tc.arguments, user_id=user_id)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )

        _log.warning("Tool loop hit MAX_TOOL_ROUNDS=%d, forcing final answer", MAX_TOOL_ROUNDS)
        final = provider.chat(messages, system=system, max_tokens=4096)
        return final.text
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

    try:
        try:
            provider = config.get_llm_provider(model, user_id=user_id)
            messages = list(history) if history else []
            messages.append({"role": "user", "content": question})

            yield from _stream_loop(provider, messages, tools, system, user_id=user_id)
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


def _stream_loop(
    provider,
    messages: list,
    tools: list[dict] | None,
    system: str,
    *,
    user_id: str,
) -> Generator[StreamEvent, None, None]:
    """Inner streaming loop with tool-call handling. ``user_id`` is required."""
    for _round in range(MAX_TOOL_ROUNDS + 1):
        text_parts: list[str] = []
        tool_calls: list[dict] = []

        for event in provider.chat_stream(
            messages,
            tools=tools,
            system=system,
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

        assistant_msg: dict = {"role": "assistant", "content": "".join(text_parts)}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)

        if not tool_calls:
            return

        for tc in tool_calls:
            result = run_tool(tc["name"], tc["arguments"], user_id=user_id)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                }
            )
        yield StreamEvent(type="text", content="\n\n")

    _log.warning(
        "Streaming tool loop hit MAX_TOOL_ROUNDS=%d, forcing final answer",
        MAX_TOOL_ROUNDS,
    )
    for event in provider.chat_stream(
        messages,
        system=system,
        max_tokens=4096,
    ):
        if event.type == "text":
            yield StreamEvent(type="text", content=event.content)
        elif event.type == "end":
            break
