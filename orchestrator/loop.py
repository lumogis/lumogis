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

import config
from models.llm import LLMEvent, LLMResponse
from models.stream import StreamEvent
from services.tools import TOOLS, run_tool

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
) -> str:
    provider = config.get_llm_provider(model)
    messages = list(history) if history else []
    messages.append({"role": "user", "content": question})

    tools = TOOLS if use_tools else None
    system = _system_prompt(use_tools)

    for _round in range(MAX_TOOL_ROUNDS + 1):
        response: LLMResponse = provider.chat(
            messages, tools=tools, system=system, max_tokens=4096,
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
            result = run_tool(tc.name, tc.arguments)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    _log.warning("Tool loop hit MAX_TOOL_ROUNDS=%d, forcing final answer", MAX_TOOL_ROUNDS)
    final = provider.chat(messages, system=system, max_tokens=4096)
    return final.text


def ask_stream(
    question: str,
    history: list | None = None,
    model: str = "claude",
    use_tools: bool = True,
) -> Generator[StreamEvent, None, None]:
    """Stream responses token-by-token from any provider."""
    try:
        provider = config.get_llm_provider(model)
        messages = list(history) if history else []
        messages.append({"role": "user", "content": question})

        tools = TOOLS if use_tools else None
        system = _system_prompt(use_tools)

        yield from _stream_loop(provider, messages, tools, system)
    except Exception as exc:
        _log.exception("ask_stream failed for model=%s", model)
        yield StreamEvent(type="error", content=_friendly_error(exc))


def _friendly_error(exc: Exception) -> str:
    """Turn raw API exceptions into short, user-facing messages."""
    msg = str(exc).lower()
    if "rate_limit" in msg or "429" in msg:
        return (
            "The AI provider's rate limit was reached. "
            "Please wait a minute and try again."
        )
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
) -> Generator[StreamEvent, None, None]:
    """Inner streaming loop with tool-call handling."""
    for _round in range(MAX_TOOL_ROUNDS + 1):
        text_parts: list[str] = []
        tool_calls: list[dict] = []

        for event in provider.chat_stream(
            messages, tools=tools, system=system, max_tokens=4096,
        ):
            if event.type == "text":
                text_parts.append(event.content)
                yield StreamEvent(type="text", content=event.content)
            elif event.type == "tool_call" and event.tool_call:
                tool_calls.append({
                    "id": event.tool_call.id,
                    "name": event.tool_call.name,
                    "arguments": event.tool_call.arguments,
                })
            elif event.type == "end":
                break

        assistant_msg: dict = {"role": "assistant", "content": "".join(text_parts)}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)

        if not tool_calls:
            return

        for tc in tool_calls:
            result = run_tool(tc["name"], tc["arguments"])
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })
        yield StreamEvent(type="text", content="\n\n")

    _log.warning(
        "Streaming tool loop hit MAX_TOOL_ROUNDS=%d, forcing final answer",
        MAX_TOOL_ROUNDS,
    )
    for event in provider.chat_stream(
        messages, system=system, max_tokens=4096,
    ):
        if event.type == "text":
            yield StreamEvent(type="text", content=event.content)
        elif event.type == "end":
            break
