# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""Anthropic LLM adapter — talks directly to the Anthropic API.

Translates canonical (OpenAI-style) messages and tools into Anthropic's
format internally. Supports both synchronous chat() and streaming
chat_stream(). If a proxy_url is provided (e.g. LiteLLM), routes
requests through it without any code changes.
"""

import logging
from typing import Generator

from anthropic import Anthropic
from models.llm import LLMEvent
from models.llm import LLMResponse
from models.llm import LLMToolCall

_log = logging.getLogger(__name__)


class AnthropicLLM:
    def __init__(self, model: str, api_key: str, base_url: str | None = None):
        self._model = model
        kwargs: dict = {"api_key": api_key, "timeout": 120.0}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = Anthropic(**kwargs)

    # -- Public API (matches LLMProvider protocol) -------------------------

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        kwargs = self._build_kwargs(messages, tools, system, max_tokens)
        response = self._client.messages.create(**kwargs)
        return self._parse_response(response)

    def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> Generator[LLMEvent, None, None]:
        kwargs = self._build_kwargs(messages, tools, system, max_tokens)
        with self._client.messages.stream(**kwargs) as stream:
            for event in stream:
                if event.type == "text":
                    yield LLMEvent(type="text", content=event.text)
                elif event.type == "content_block_stop":
                    block = event.content_block
                    if hasattr(block, "type") and block.type == "tool_use":
                        yield LLMEvent(
                            type="tool_call",
                            tool_call=LLMToolCall(
                                id=block.id,
                                name=block.name,
                                arguments=block.input,
                            ),
                        )
            final = stream.get_final_message()
        stop = "tool_calls" if final.stop_reason == "tool_use" else "stop"
        yield LLMEvent(type="end", stop_reason=stop)

    # -- Internals ---------------------------------------------------------

    def _build_kwargs(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        system: str | None,
        max_tokens: int,
    ) -> dict:
        anthropic_messages = [self._translate_message(m) for m in messages]
        kwargs: dict = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": anthropic_messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [self._translate_tool(t) for t in tools]
        return kwargs

    @staticmethod
    def _translate_message(msg: dict) -> dict:
        """Canonical (OpenAI-style) message -> Anthropic message format."""
        role = msg["role"]

        if role == "tool":
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": msg["tool_call_id"],
                        "content": msg["content"],
                    }
                ],
            }

        if role == "assistant":
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls", [])
            if not tool_calls:
                return {"role": "assistant", "content": content or ""}

            blocks: list[dict] = []
            if content:
                blocks.append({"type": "text", "text": content})
            for tc in tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc["arguments"],
                    }
                )
            return {"role": "assistant", "content": blocks}

        return {"role": role, "content": msg.get("content", "")}

    @staticmethod
    def _translate_tool(tool: dict) -> dict:
        """Canonical tool definition -> Anthropic tool format."""
        return {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "input_schema": tool.get("parameters", tool.get("input_schema", {})),
        }

    @staticmethod
    def _parse_response(response) -> LLMResponse:
        text_parts: list[str] = []
        tool_calls: list[LLMToolCall] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(LLMToolCall(id=block.id, name=block.name, arguments=block.input))

        stop = "tool_calls" if response.stop_reason == "tool_use" else "stop"
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=stop,
        )
