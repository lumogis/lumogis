# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Port: injection scanner for live MCP tool results (LUM-362).

Third injection surface after document ingest (LUM-127) and user config
(LUM-361): a connector result (email body, calendar note, vault file) returned
mid-session is injected into the LLM context as a trusted turn, so it must be
scanned before it reaches the model.
"""

from typing import Protocol
from typing import TypedDict
from typing import runtime_checkable


class ToolResultScanResult(TypedDict, total=False):
    """Outcome of scanning a single tool result.

    ``safe_text`` is what should be injected into LLM context: the original
    content when clean, or a redaction placeholder when ``flagged``. Callers
    keep the raw result for the audit log (LUM-362 logs flagged raw content for
    forensics — unlike LUM-361, which must never log the secret).
    """

    flagged: bool
    safe_text: str
    pattern_hits: list[str]


@runtime_checkable
class ToolResultScanner(Protocol):
    def scan(self, text: str) -> ToolResultScanResult:
        """Inspect a tool result; ``flagged`` gates redaction before injection."""

        ...
