"""Playwright-based JS-rendered page fetcher (optional adapter).

Active only when:
  - PLAYWRIGHT_ENABLED=true in environment
  - The docker-compose.playwright.yml overlay is running (provides the
    Playwright service at PLAYWRIGHT_URL, default http://playwright:3000)

Falls back to page_scraper transparently when unavailable.
Playwright itself is NOT in base requirements.txt; import is guarded.
"""

import logging
import os
import uuid
from datetime import datetime, timezone

from models.signals import Signal, SourceConfig

_log = logging.getLogger(__name__)

_ENABLED = os.environ.get("PLAYWRIGHT_ENABLED", "false").lower() == "true"


class PlaywrightFetcher:
    def __init__(self, config: SourceConfig) -> None:
        self._config = config
        self._playwright_url = os.environ.get("PLAYWRIGHT_URL", "http://playwright:3000")

    # ------------------------------------------------------------------
    # Public interface (SignalSource protocol)
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        if not _ENABLED:
            return False
        try:
            import httpx

            resp = httpx.get(f"{self._playwright_url}/health", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def poll(self) -> list[Signal]:
        """Fetch JS-rendered page. Falls back to page_scraper if unavailable."""
        if not _ENABLED:
            _log.debug("Playwright disabled, falling back to page_scraper")
            return self._fallback()

        html = self._fetch_via_playwright(self._config.url)
        if not html:
            _log.warning(
                "Playwright fetch failed for %s, falling back to page_scraper",
                self._config.url,
            )
            return self._fallback()

        try:
            import trafilatura

            text = trafilatura.extract(html) or ""
        except ImportError:
            text = html[:4000]

        if not text.strip():
            return self._fallback()

        title = self._config.name
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

    def _fetch_via_playwright(self, url: str) -> str | None:
        """POST to the Playwright service and return rendered HTML."""
        try:
            import httpx

            resp = httpx.post(
                f"{self._playwright_url}/render",
                json={"url": url},
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json().get("html", "")
        except Exception as exc:
            _log.error("Playwright service error for %s: %s", url, exc)
        return None

    def _fallback(self) -> list[Signal]:
        from adapters.page_scraper import PageScraper

        return PageScraper(self._config).poll()
