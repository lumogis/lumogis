# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Default no-op scanner (LUM-127). Keeps sanitiser YAML path stable."""

import logging

from ports.injection_scanner import InjectionScanResult

_log = logging.getLogger(__name__)


class NullInjectionScanner:
    """Passthrough scanner for environments without external scanners."""

    def scan(self, text: str) -> InjectionScanResult:
        _log.debug("NullInjectionScanner: passthrough (%d chars)", len(text))
        return {"flagged": False, "adjusted_text": text, "patterns": []}
