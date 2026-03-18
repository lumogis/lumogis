"""Port: LLM provider protocol.

Any adapter that implements chat() and chat_stream() with the canonical
message/tool format can be plugged in via config.get_llm_provider().
"""

from typing import Generator, Protocol

from models.llm import LLMEvent, LLMResponse


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
