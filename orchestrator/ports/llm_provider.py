# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Port: LLM provider protocol.

Any adapter that implements chat() and chat_stream() with the canonical
message/tool format can be plugged in via config.get_llm_provider().
"""

from typing import Generator
from typing import Protocol

from models.llm import LLMEvent
from models.llm import LLMResponse


class LLMProvider(Protocol):
    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse: ...

    def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> Generator[LLMEvent, None, None]: ...
