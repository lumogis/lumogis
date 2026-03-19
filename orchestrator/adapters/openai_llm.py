# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""OpenAI-compatible LLM adapter.

Works with any backend that speaks the OpenAI chat completions API:
Ollama, ChatGPT, Perplexity, Groq, Mistral, Together AI, etc.

Supports both synchronous chat() and real token-by-token streaming via
chat_stream(). If a proxy_url is configured, it overrides base_url to
route through LiteLLM (or any compatible proxy) for rate limiting and
observability.
"""

import json
import logging
from typing import Generator

from models.llm import LLMEvent
from models.llm import LLMResponse
from models.llm import LLMToolCall
from openai import OpenAI

_log = logging.getLogger(__name__)

_NOT_GIVEN = object()


class OpenAILLM:
    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        context_budget: int | None = None,
    ):
        self._model = model
        self._is_ollama = "ollama" in (base_url or "").lower()
        self._context_budget = context_budget
        kwargs: dict = {"timeout": 120.0}
        if base_url:
            kwargs["base_url"] = base_url
        kwargs["api_key"] = api_key or "not-needed"
        self._client = OpenAI(**kwargs)

    # -- Public API (matches LLMProvider protocol) -------------------------

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        kwargs = self._build_kwargs(messages, tools, system, max_tokens)
        response = self._client.chat.completions.create(**kwargs)
        return self._parse_response(response)

    def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> Generator[LLMEvent, None, None]:
        kwargs = self._build_kwargs(messages, tools, system, max_tokens)
        kwargs["stream"] = True
        stream = self._client.chat.completions.create(**kwargs)

        tool_calls_acc: dict[int, dict] = {}

        for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            if not choice:
                continue
            delta = choice.delta

            if delta and delta.content:
                yield LLMEvent(type="text", content=delta.content)

            if delta and delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": tc_delta.id or "",
                            "name": "",
                            "arguments": "",
                        }
                    acc = tool_calls_acc[idx]
                    if tc_delta.id:
                        acc["id"] = tc_delta.id
                    if tc_delta.function and tc_delta.function.name:
                        acc["name"] = tc_delta.function.name
                    if tc_delta.function and tc_delta.function.arguments:
                        acc["arguments"] += tc_delta.function.arguments

            if choice.finish_reason:
                for acc in tool_calls_acc.values():
                    try:
                        args = json.loads(acc["arguments"]) if acc["arguments"] else {}
                    except json.JSONDecodeError:
                        args = {}
                    yield LLMEvent(
                        type="tool_call",
                        tool_call=LLMToolCall(
                            id=acc["id"],
                            name=acc["name"],
                            arguments=args,
                        ),
                    )
                stop = "tool_calls" if choice.finish_reason == "tool_calls" else "stop"
                yield LLMEvent(type="end", stop_reason=stop)
                return

        yield LLMEvent(type="end", stop_reason="stop")

    # -- Internals ---------------------------------------------------------

    def _build_kwargs(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        system: str | None,
        max_tokens: int,
    ) -> dict:
        oai_messages: list[dict] = []
        if system:
            oai_messages.append({"role": "system", "content": system})

        for msg in messages:
            oai_messages.append(self._translate_message(msg))

        kwargs: dict = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": oai_messages,
        }
        if tools:
            kwargs["tools"] = [self._translate_tool(t) for t in tools]
        if self._is_ollama and self._context_budget:
            kwargs.setdefault("extra_body", {})["options"] = {"num_ctx": self._context_budget}
        return kwargs

    @staticmethod
    def _translate_message(msg: dict) -> dict:
        """Canonical message -> OpenAI message format."""
        role = msg["role"]

        if role == "tool":
            return {
                "role": "tool",
                "tool_call_id": msg["tool_call_id"],
                "content": msg["content"],
            }

        if role == "assistant":
            tool_calls = msg.get("tool_calls", [])
            if not tool_calls:
                return {"role": "assistant", "content": msg.get("content", "")}
            oai_tool_calls = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["arguments"])
                        if isinstance(tc["arguments"], dict)
                        else tc["arguments"],
                    },
                }
                for tc in tool_calls
            ]
            result: dict = {
                "role": "assistant",
                "tool_calls": oai_tool_calls,
            }
            content = msg.get("content", "")
            if content:
                result["content"] = content
            return result

        return {"role": role, "content": msg.get("content", "")}

    @staticmethod
    def _translate_tool(tool: dict) -> dict:
        """Canonical tool definition -> OpenAI tool format."""
        return {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("parameters", tool.get("input_schema", {})),
            },
        }

    @staticmethod
    def _parse_response(response) -> LLMResponse:
        choice = response.choices[0]
        message = choice.message

        text = message.content or ""
        tool_calls: list[LLMToolCall] = []

        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(LLMToolCall(id=tc.id, name=tc.function.name, arguments=args))

        stop = "tool_calls" if choice.finish_reason == "tool_calls" else "stop"
        return LLMResponse(text=text, tool_calls=tool_calls, stop_reason=stop)
