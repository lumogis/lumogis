# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Port: secrets scanner for user-authored config files (LUM-361).

Sibling of :mod:`ports.injection_scanner`. Where the injection scanner guards
*ingested external content* against prompt injection, this guards *user-created
Lumogis config files* (pipe.md, procedures, LUMOGIS_POLICY.md, WAKE.md) against
accidentally embedded credentials before they reach the LLM context.
"""

from typing import Protocol
from typing import TypedDict
from typing import runtime_checkable


class SecretMatch(TypedDict):
    """A single detected credential — carries the line for user remediation only.

    The line number lets the caller show the operator *where* to fix the file.
    The raw matched value is never returned or logged (secrets in logs = double
    exposure); only the pattern id/name and the 1-based line are surfaced.
    """

    pattern_id: str
    name: str
    line: int


class SecretsScanResult(TypedDict, total=False):
    """Outcome of scanning a config file's content."""

    blocked: bool
    matches: list[SecretMatch]


@runtime_checkable
class SecretsScanner(Protocol):
    def scan(self, text: str) -> SecretsScanResult:
        """Inspect config-file text for credentials; ``blocked`` gates the load."""

        ...
