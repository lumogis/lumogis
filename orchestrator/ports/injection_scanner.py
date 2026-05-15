# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Port: optional external injection scanner."""

from typing import Protocol
from typing import TypedDict
from typing import runtime_checkable


class InjectionScanResult(TypedDict, total=False):
    """Result of scanning adjusted corpus text."""

    flagged: bool
    adjusted_text: str
    patterns: list[str]


@runtime_checkable
class InjectionScanner(Protocol):
    def scan(self, text: str) -> InjectionScanResult:
        """Inspect text for injection markers; adapters may augment pattern hits."""

        ...
