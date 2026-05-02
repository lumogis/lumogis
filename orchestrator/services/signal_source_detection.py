# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Detect RSS vs page source from a URL for the signals /sources flow.

Routed from :func:`routes.signals` so ``orchestrator/routes/*`` does not import
``adapters/*`` directly (architectural boundary; see
``test_routes_no_adapter_imports``).
"""

from __future__ import annotations


def detect_signal_source(url: str) -> dict:
    """Try to detect the source type and return up to 3 preview items.

    Detection order:
      1. Try RSS/Atom auto-detection (feedparser + link tag scanning).
      2. Fall back to page scraping via trafilatura.
    """
    from adapters.page_scraper import PageScraper
    from adapters.rss_source import RSSSource

    feed_url, preview_items = RSSSource.detect(url)
    if feed_url and preview_items:
        return {
            "source_type": "rss",
            "feed_url": feed_url,
            "preview_items": preview_items,
        }

    page_items = PageScraper.detect(url)
    return {
        "source_type": "page",
        "feed_url": None,
        "preview_items": page_items,
    }
