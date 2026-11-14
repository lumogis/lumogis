# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Static HTML page scraper adapter.

Uses trafilatura to extract readable text content from static web pages.
Returns normalised Signal objects.  Used by signals/page_monitor.py for
change detection and as the fallback when playwright_fetcher is unavailable.
"""

import logging
import uuid
from datetime import datetime
from datetime import timezone

from models.signals import Signal
from models.signals import SourceConfig

from services import outbound_signal_fetch

_log = logging.getLogger(__name__)

# Bounded so a poll cannot hold a scheduler thread indefinitely.
_FETCH_TIMEOUT_S = 15.0


class PageScraper:
    def __init__(self, config: SourceConfig) -> None:
        self._config = config
        # LUM-841: whether the most recent `_fetch_html` reached the host at
        # all. Declared here rather than set only in the failure path, so
        # `detect` never has to guess at a missing attribute.
        self.last_fetch_ok: bool = True

    # ------------------------------------------------------------------
    # Public interface (SignalSource protocol)
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        try:
            return self._fetch_html(self._config.url) is not None
        except Exception:
            # Includes OutboundURLBlocked: a URL policy refuses is not reachable
            # in any sense a caller of `ping` cares about.
            return False

    def poll(self) -> list[Signal]:
        """Fetch page, extract text, return a single Signal."""
        import trafilatura  # Docker-only dep; lazy to keep local tests dependency-free

        html = self._fetch_html(self._config.url)
        if not html:
            return []

        text = trafilatura.extract(html) or ""
        if not text.strip():
            _log.debug("trafilatura returned empty content for %s", self._config.url)
            return []

        # Attempt to extract a title from the HTML <title> tag.
        title = self._extract_title(html) or self._config.name

        return [
            Signal(
                signal_id=str(uuid.uuid4()),
                source_id=self._config.id,
                title=title,
                url=self._config.url,
                published_at=None,
                content_summary="",
                raw_content=text,
                entities=[],
                topics=[],
                importance_score=0.0,
                relevance_score=0.0,
                notified=False,
                created_at=datetime.now(timezone.utc),
                user_id=self._config.user_id,
            )
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fetch_html(self, url: str) -> str | None:
        """Bounded, policy-checked fetch (LUM-838/841).

        Raises :class:`OutboundURLBlocked` when policy refuses the URL or a
        redirect target — deliberately not swallowed into ``last_fetch_ok``,
        because "we will not fetch that" is a different answer from "it did not
        respond" and only one of them is something the user can fix by waiting.
        """
        text = outbound_signal_fetch.fetch_text(url, timeout=_FETCH_TIMEOUT_S)
        # LUM-841: record whether the fetch itself succeeded. Returning None
        # alone cannot distinguish "the host did not answer" from "the page had
        # nothing extractable", and detection collapsed both into a `page`
        # source with no items — so a typo'd hostname read as "we'll watch this
        # page" and then polled nothing forever.
        self.last_fetch_ok = text is not None
        if text is None:
            _log.error("PageScraper fetch failed for %s", url)
        return text

    @staticmethod
    def _extract_title(html: str) -> str | None:
        import re

        match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None

    # ------------------------------------------------------------------
    # Class-level helper for source detection (used by routes/signals.py)
    # ------------------------------------------------------------------

    @classmethod
    def detect(cls, url: str) -> tuple[list[dict], bool]:
        """Return ``(preview_items, reachable)`` without creating a SourceConfig.

        Used by ``POST /sources`` detection when no feed is found.

        ``reachable`` is **False only when the fetch itself failed** — DNS,
        connection refused, timeout, or a non-2xx status. A page that answered
        and simply had no extractable text is reachable with zero items, which
        is a perfectly good page source. Conflating the two is LUM-841: both
        used to produce an empty list, so the route could not tell a typo from
        a text-light page, and told the user the reassuring one.
        """
        dummy = SourceConfig(
            id="__detect__",
            name="detect",
            source_type="page",
            url=url,
            category="",
            active=False,
            poll_interval=3600,
            extraction_method="trafilatura",
            css_selector_override=None,
            last_polled_at=None,
            last_signal_at=None,
        )
        instance = cls(dummy)
        signals = instance.poll()
        items = []
        for s in signals[:3]:
            items.append(
                {
                    "title": s.title,
                    "url": s.url,
                    "published_at": "",
                    "summary": s.raw_content[:200],
                }
            )
        # Plain attribute access, not `getattr(..., True)`. `__init__` declares
        # the attribute, so a default could never fire — and it would default in
        # the WRONG direction: if a later refactor renamed the flag, a silent
        # `True` is "reachable", which is exactly the false reassurance this
        # whole change exists to remove. An AttributeError is the honest failure.
        return items, instance.last_fetch_ok
