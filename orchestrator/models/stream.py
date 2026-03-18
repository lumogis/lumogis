"""Stream event types for the ask_stream() generator."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class StreamEvent:
    type: Literal["text", "tool_status", "error"]
    content: str
