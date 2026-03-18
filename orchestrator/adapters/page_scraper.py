"""Static HTML page scraper adapter.

Uses trafilatura to extract readable text content from static web pages.
Returns normalised Signal objects.  Used by signals/page_monitor.py for
change detection and as the fallback when playwright_fetcher is unavailable.
"""

import logging
import uuid
from datetime import datetime, timezone

import httpx
import trafilatura

from models.signals import Signal, SourceConfig

_log = logging.getLogger(__name__)


class PageScraper:
    def __init__(self, config: SourceConfig) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Public interface (SignalSource protocol)
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        try:
            resp = httpx.get(self._config.url, timeout=8, follow_redirects=True)
            return resp.status_code == 200
        except Exception:
            return False

    def poll(self) -> list[Signal]:
        """Fetch page, extract text, return a single Signal."""
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
        try:
            resp = httpx.get(url, timeout=15, follow_redirects=True)
            resp.raise_for_status()
            return resp.text
        except Exception as exc:
            _log.error("PageScraper fetch error for %s: %s", url, exc)
            return None

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
    def detect(cls, url: str) -> list[dict]:
        """Return up to 3 preview items without creating a SourceConfig.

        Used by POST /sources detection when no feed is found.
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
        return items
