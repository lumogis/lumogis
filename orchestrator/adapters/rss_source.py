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

from models.signals import Signal
from models.signals import SourceConfig

from services import outbound_signal_fetch

_log = logging.getLogger(__name__)

# Bounded so no detection path can hold a request thread indefinitely (LUM-843).
_DETECT_TIMEOUT_S = 8.0
_PROBE_TIMEOUT_S = 5.0

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
        # Whether the most recent `poll()` actually reached the feed. Mirrors
        # `PageScraper.last_fetch_ok` so `_poll_signal_source` can ask the same
        # question of either adapter.
        self.last_fetch_ok: bool = True

    # ------------------------------------------------------------------
    # Public interface (SignalSource protocol)
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        try:
            url = self._get_feed_url()
            return self._fetch_text(url, timeout=_DETECT_TIMEOUT_S) is not None
        except Exception:
            # Includes OutboundURLBlocked: a URL policy refuses is not reachable
            # in any sense a caller of `ping` cares about.
            return False

    def poll(self) -> list[Signal]:
        """Fetch and normalise the feed. Returns up to 20 most-recent signals.

        Sets :attr:`last_fetch_ok` so a caller can tell "the feed did not answer"
        from "the feed answered with no new entries" — the two produce the same
        empty list, and ``_poll_signal_source`` records health from this flag
        rather than from an exception, because there is no exception to catch.

        The fetch is bounded (LUM-843). ``feedparser.parse(url)`` fetches through
        urllib with the OS default socket timeout — on a blackholed host, minutes
        — which on this path would hold a scheduler thread rather than a request
        thread. Fetching the body ourselves and parsing the string keeps it to
        one bounded call.
        """
        import feedparser  # Docker-only dep; lazy to keep local tests dependency-free

        url = self._get_feed_url()
        body = self._fetch_text(url, timeout=_DETECT_TIMEOUT_S)
        if body is None:
            _log.error("RSS fetch failed for %s", url)
            self.last_fetch_ok = False
            return []

        try:
            feed = feedparser.parse(body)
        except Exception as exc:
            _log.error("RSS parse error for %s: %s", url, exc)
            # The host answered; we could not read what it said. That is a
            # source-level failure either way, so it counts as not-ok.
            self.last_fetch_ok = False
            return []

        self.last_fetch_ok = True
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
        """Find this source's feed URL, fetching the base address **once**.

        LUM-843. This used to fetch ``base`` twice — first by handing the URL to
        ``feedparser.parse``, then again through ``_detect_from_page_head`` — and
        then probe eight common feed paths regardless of whether the host had
        answered either time. Against a blackholed address that was up to eleven
        sequential attempts on one click, two of them **unbounded**:
        ``feedparser.parse(url)`` fetches through urllib with the OS default
        socket timeout, roughly two minutes.

        Now: one bounded fetch of ``base``, whose body serves both questions (is
        it already a feed? does it name one in a ``<link>`` tag?), and the probe
        ladder is skipped entirely when that fetch failed. A host that does not
        answer is answered for in one timeout instead of ten.
        """
        import feedparser  # Docker-only dep; lazy to keep local tests dependency-free

        base = self._config.url
        body = self._fetch_text(base, timeout=_DETECT_TIMEOUT_S)

        if body is None:
            # The host did not answer. Probing eight paths off a root that does
            # not resolve is eight guaranteed failures, so stop here — and say
            # so, rather than letting a caller infer health from an empty list.
            self.last_fetch_ok = False
            _log.info("rss_detect_unreachable url=%s", base)
            return base

        # 1. Is the body we already have a feed? `parse` on a string does no
        #    network, so this costs nothing beyond the fetch above.
        try:
            feed = feedparser.parse(body)
            if feed.entries or feed.feed.get("title"):
                _log.debug("Base URL is already a feed: %s", base)
                return base
        except Exception:
            pass

        # 2. Same body, second question: does it name a feed in a <link> tag?
        detected = self._feed_link_in(base, body)
        if detected:
            return detected

        # 3. Only now probe common feed paths off the site root. The host is
        #    known to answer, so these are worth spending.
        parsed = urlparse(base)
        root = f"{parsed.scheme}://{parsed.netloc}"
        for path in _COMMON_FEED_PATHS:
            candidate = root + path
            candidate_body = self._fetch_text(candidate, timeout=_PROBE_TIMEOUT_S)
            if candidate_body is None:
                continue
            try:
                feed = feedparser.parse(candidate_body)
            except Exception:
                continue
            if feed.entries:
                _log.info("Feed detected at common path: %s", candidate)
                return candidate

        # 4. Fall back to base URL (may be a valid feed that feedparser failed on above).
        _log.debug("No feed auto-detected for %s, using base URL", base)
        return base

    def _fetch_text(self, url: str, *, timeout: float) -> str | None:
        """One bounded, policy-checked fetch. ``None`` = the host did not answer.

        Every network call this adapter makes goes through here — detection and
        poll alike — so no path can acquire an unbounded fetch by accident (which
        is how ``feedparser.parse(url)`` came to hold a request thread for
        minutes, LUM-843) and none can skip outbound validation (LUM-838).

        Raises :class:`OutboundURLBlocked` when policy refuses the URL or a
        redirect target. That propagates rather than becoming ``None``: a
        refusal is a decision the product can explain, and reporting it as "did
        not answer" would bury it.
        """
        return outbound_signal_fetch.fetch_text(url, timeout=timeout)

    def _feed_link_in(self, url: str, html: str) -> str | None:
        """Find a feed named by a ``<link rel="alternate">`` tag in `html`.

        LUM-843: takes the body rather than fetching it. The caller already has
        it, and re-fetching was one of the duplicate requests that made an
        unreachable host so slow to report.
        """
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
    def detect(cls, url: str) -> tuple[str | None, list[dict], bool]:
        """Return ``(feed_url, preview_items[3], reachable)``.

        Used by the ``POST /sources`` detection step. ``feed_url`` is ``None``
        when no feed was found.

        ``reachable`` reports whether the host answered the **first** contact
        (LUM-843). It matters because detection previously decided reachability
        from the *last* of up to eleven fetches — so a site that rate-limited
        the tail of the ladder was reported as unreachable despite having
        answered moments earlier, and the caller was told to check for a typo.
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
        # Bounded: fetch the body ourselves and parse the string, so this cannot
        # become an unbounded urllib fetch inside feedparser (LUM-843).
        body = instance._fetch_text(feed_url, timeout=_DETECT_TIMEOUT_S)
        if body is None:
            return None, [], instance.last_fetch_ok
        try:
            feed = feedparser.parse(body)
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
                return feed_url, items, True
        except Exception:
            pass
        return None, [], instance.last_fetch_ok
