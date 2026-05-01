# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""RSS/Atom/JSON feed adapter.

Auto-detects the feed URL from a base URL by:
1. Fetching the page and scanning <link rel="alternate"> tags in the HTML head.
2. Probing common feed paths (/feed, /rss, /atom, /feed.xml, /rss.xml, /atom.xml).
3. Trying the base URL directly as a feed.

Detection result is cached on the instance so it runs once per source lifecycle.
"""

import logging
import re
import time
import uuid
from datetime import datetime
from datetime import timezone
from urllib.parse import urljoin
from urllib.parse import urlparse

import httpx
from models.signals import Signal
from models.signals import SourceConfig

_log = logging.getLogger(__name__)

_COMMON_FEED_PATHS = [
    "/feed",
    "/rss",
    "/atom",
    "/feed.xml",
    "/rss.xml",
    "/atom.xml",
    "/feed.json",
    "/index.xml",
]

_LINK_TAG_RE = re.compile(
    r'<link[^>]+rel=["\']alternate["\'][^>]+>',
    re.IGNORECASE,
)
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
_TYPE_RE = re.compile(r'type=["\']([^"\']+)["\']', re.IGNORECASE)


class RSSSource:
    def __init__(self, config: SourceConfig) -> None:
        self._config = config
        self._feed_url: str | None = None

    # ------------------------------------------------------------------
    # Public interface (SignalSource protocol)
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        try:
            url = self._get_feed_url()
            resp = httpx.get(url, timeout=8, follow_redirects=True)
            return resp.status_code == 200
        except Exception:
            return False

    def poll(self) -> list[Signal]:
        """Fetch and normalise the feed. Returns up to 20 most-recent signals."""
        import feedparser  # Docker-only dep; lazy to keep local tests dependency-free

        url = self._get_feed_url()
        try:
            feed = feedparser.parse(url)
        except Exception as exc:
            _log.error("RSS parse error for %s: %s", url, exc)
            return []

        if not feed.entries:
            _log.debug("No entries in feed %s", url)
            return []

        signals: list[Signal] = []
        for entry in feed.entries[:20]:
            raw = (
                entry.get("summary", "") or (entry.get("content") or [{}])[0].get("value", "") or ""
            )
            signals.append(
                Signal(
                    signal_id=str(uuid.uuid4()),
                    source_id=self._config.id,
                    title=entry.get("title", "").strip(),
                    url=entry.get("link", ""),
                    published_at=self._parse_time(entry.get("published_parsed")),
                    content_summary="",
                    raw_content=raw,
                    entities=[],
                    topics=[],
                    importance_score=0.0,
                    relevance_score=0.0,
                    notified=False,
                    created_at=datetime.now(timezone.utc),
                    user_id=self._config.user_id,
                )
            )
        _log.info("RSSSource: polled %d entries from %s", len(signals), url)
        return signals

    # ------------------------------------------------------------------
    # Feed URL detection (runs once per instance)
    # ------------------------------------------------------------------

    def _get_feed_url(self) -> str:
        if self._feed_url is None:
            self._feed_url = self._detect_feed_url()
        return self._feed_url

    def _detect_feed_url(self) -> str:
        import feedparser  # Docker-only dep; lazy to keep local tests dependency-free

        base = self._config.url

        # 1. Check if the base URL is already a valid feed.
        try:
            feed = feedparser.parse(base)
            if feed.entries or feed.feed.get("title"):
                _log.debug("Base URL is already a feed: %s", base)
                return base
        except Exception:
            pass

        # 2. Fetch page HTML and look for <link rel="alternate"> tags.
        detected = self._detect_from_page_head(base)
        if detected:
            return detected

        # 3. Probe common feed paths off the site root.
        parsed = urlparse(base)
        root = f"{parsed.scheme}://{parsed.netloc}"
        for path in _COMMON_FEED_PATHS:
            candidate = root + path
            try:
                resp = httpx.get(candidate, timeout=5, follow_redirects=True)
                if resp.status_code == 200:
                    feed = feedparser.parse(resp.text)
                    if feed.entries:
                        _log.info("Feed detected at common path: %s", candidate)
                        return candidate
            except Exception:
                continue

        # 4. Fall back to base URL (may be a valid feed that feedparser failed on above).
        _log.debug("No feed auto-detected for %s, using base URL", base)
        return base

    def _detect_from_page_head(self, url: str) -> str | None:
        try:
            resp = httpx.get(url, timeout=8, follow_redirects=True)
            html = resp.text
        except Exception as exc:
            _log.debug("Could not fetch page for feed detection %s: %s", url, exc)
            return None

        for tag in _LINK_TAG_RE.findall(html):
            type_match = _TYPE_RE.search(tag)
            if not type_match:
                continue
            link_type = type_match.group(1).lower()
            if not ("rss" in link_type or "atom" in link_type or "json" in link_type):
                continue
            href_match = _HREF_RE.search(tag)
            if not href_match:
                continue
            href = href_match.group(1)
            feed_url = urljoin(url, href)
            _log.info("Feed found via <link> tag: %s -> %s", url, feed_url)
            return feed_url
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_time(time_struct) -> datetime | None:
        if time_struct is None:
            return None
        try:
            return datetime.fromtimestamp(time.mktime(time_struct), tz=timezone.utc)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Class-level helper for source detection (used by routes/signals.py)
    # ------------------------------------------------------------------

    @classmethod
    def detect(cls, url: str) -> tuple[str | None, list[dict]]:
        """Return (feed_url, preview_items[3]) without creating a SourceConfig.

        Used by POST /sources detection step. feed_url is None if no feed found.
        """
        dummy = SourceConfig(
            id="__detect__",
            name="detect",
            source_type="rss",
            url=url,
            category="",
            active=False,
            poll_interval=3600,
            extraction_method="feedparser",
            css_selector_override=None,
            last_polled_at=None,
            last_signal_at=None,
        )
        import feedparser  # Docker-only dep; lazy to keep local tests dependency-free

        instance = cls(dummy)
        feed_url = instance._detect_feed_url()
        try:
            feed = feedparser.parse(feed_url)
            items = []
            for entry in feed.entries[:3]:
                items.append(
                    {
                        "title": entry.get("title", ""),
                        "url": entry.get("link", ""),
                        "published_at": str(entry.get("published", "")),
                        "summary": (entry.get("summary", "") or "")[:200],
                    }
                )
            if items:
                return feed_url, items
        except Exception:
            pass
        return None, []
