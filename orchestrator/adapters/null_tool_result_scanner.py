# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Null tool-result scanner (LUM-362) — passthrough for tests / disabled scans.

Returned when ``TOOL_RESULT_SCANNER_ENABLED=false`` or in test environments.
Mirrors :class:`adapters.null_injection_scanner.NullInjectionScanner`.
"""

import logging

from ports.tool_result_scanner import ToolResultScanResult

_log = logging.getLogger(__name__)


class NullToolResultScanner:
    """Passthrough scanner: never flags, returns the text unchanged."""

    def scan(self, text: str) -> ToolResultScanResult:
        _log.debug("NullToolResultScanner: passthrough (%d chars)", len(text))
        return {"flagged": False, "safe_text": text, "pattern_hits": []}
