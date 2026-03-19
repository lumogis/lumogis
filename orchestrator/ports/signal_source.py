# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""Port: signal source protocol.

Implemented by rss_source, page_scraper, playwright_fetcher, calendar_adapter.
"""

from typing import Protocol, runtime_checkable

from models.signals import Signal


@runtime_checkable
class SignalSource(Protocol):
    def poll(self) -> list[Signal]:
        """Fetch new signals from this source. Returns empty list on failure."""
        ...

    def ping(self) -> bool:
        """Return True if the source is reachable."""
        ...
