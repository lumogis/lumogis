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

    The result carries **``reachable``** (LUM-841). Before it, a host that did
    not answer and a page with nothing to extract both produced
    ``{"source_type": "page", "preview_items": []}`` — indistinguishable to
    every caller. The first is a perfectly good page source; the second is a
    typo, and the flow told the user the reassuring one and then polled nothing
    forever. A feed that parsed is reachable by construction.
    """
    from adapters.page_scraper import PageScraper
    from adapters.rss_source import RSSSource

    feed_url, preview_items, rss_reachable = RSSSource.detect(url)
    if feed_url and preview_items:
        return {
            "source_type": "rss",
            "feed_url": feed_url,
            "preview_items": preview_items,
            "reachable": True,
        }

    if not rss_reachable:
        # The host did not answer the RSS path's very first, bounded fetch of
        # this exact URL. Handing the same address to the page scraper would buy
        # one more timeout and the same answer, so stop here (LUM-843) — and
        # take reachability from that FIRST contact rather than a later one.
        return {
            "source_type": "page",
            "feed_url": None,
            "preview_items": [],
            "reachable": False,
        }

    page_items, reachable = PageScraper.detect(url)
    return {
        "source_type": "page",
        "feed_url": None,
        "preview_items": page_items,
        "reachable": reachable,
    }
