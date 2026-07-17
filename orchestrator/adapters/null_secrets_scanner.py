# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Null secrets scanner (LUM-361) — passthrough for tests / disabled scans.

Mirrors :class:`adapters.null_injection_scanner.NullInjectionScanner`: returned
when ``SECRETS_SCANNER_ENABLED=false`` or in test environments that intentionally
load config fixtures containing placeholder credentials.
"""

import logging

from ports.secrets_scanner import SecretsScanResult

_log = logging.getLogger(__name__)


class NullSecretsScanner:
    """Passthrough scanner: never blocks."""

    def scan(self, text: str) -> SecretsScanResult:
        _log.debug("NullSecretsScanner: passthrough (%d chars)", len(text))
        return {"blocked": False, "matches": []}
