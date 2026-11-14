# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Source detection reports reachability separately from emptiness (LUM-841).

Before this, `detect_signal_source` collapsed two very different outcomes into
one shape: a host that did not answer and a page that answered with nothing
extractable both produced `{"source_type": "page", "preview_items": []}`. The
first is a typo; the second is a perfectly good page source. The route could not
tell them apart, so it reported the reassuring one — a mistyped hostname read as
"we'll watch this page" and then polled nothing, forever.

`trafilatura` and `feedparser` are Docker-only dependencies, so these tests
drive the adapters through their own seams rather than importing them.
"""

from __future__ import annotations

import pytest
from services.signal_source_detection import detect_signal_source


class _FakeRSS:
    """Stands in for `RSSSource.detect` — returns (feed_url, items, reachable)."""

    result: tuple[str | None, list[dict], bool] = (None, [], True)
    calls: int = 0

    @classmethod
    def detect(cls, url):
        cls.calls += 1
        return cls.result


class _FakePage:
    """Stands in for `PageScraper.detect` — returns (items, reachable)."""

    result: tuple[list[dict], bool] = ([], True)
    calls: int = 0

    @classmethod
    def detect(cls, url):
        cls.calls += 1
        return cls.result


@pytest.fixture
def adapters(monkeypatch):
    """Patch the two adapters at their import sites inside the service."""
    import adapters.page_scraper as page_scraper
    import adapters.rss_source as rss_source

    _FakeRSS.result = (None, [], True)
    _FakePage.result = ([], True)
    _FakeRSS.calls = 0
    _FakePage.calls = 0
    monkeypatch.setattr(rss_source, "RSSSource", _FakeRSS)
    monkeypatch.setattr(page_scraper, "PageScraper", _FakePage)
    return _FakeRSS, _FakePage


def test_an_unreachable_host_is_not_reachable(adapters):
    _rss, page = adapters
    page.result = ([], False)
    out = detect_signal_source("https://nope.invalid")
    assert out["source_type"] == "page"
    assert out["preview_items"] == []
    assert out["reachable"] is False


def test_a_reachable_page_with_nothing_extractable_is_still_reachable(adapters):
    """The case that must NOT be reported as unreachable: the host answered.

    Identical `preview_items` to the test above, and the opposite meaning —
    which is exactly why the flag has to exist rather than being inferred from
    an empty list.
    """
    _rss, page = adapters
    page.result = ([], True)
    out = detect_signal_source("https://example.com/sparse")
    assert out["source_type"] == "page"
    assert out["preview_items"] == []
    assert out["reachable"] is True


def test_a_page_with_content_is_reachable(adapters):
    _rss, page = adapters
    page.result = ([{"title": "Hello", "url": "https://example.com"}], True)
    out = detect_signal_source("https://example.com")
    assert out["reachable"] is True
    assert out["preview_items"]


def test_a_parsed_feed_is_reachable_by_construction(adapters):
    """A feed that parsed was fetched, so the page path never runs."""
    rss, page = adapters
    rss.result = ("https://example.com/feed.xml", [{"title": "Item"}], True)
    page.result = ([], False)  # would say unreachable if it were consulted
    out = detect_signal_source("https://example.com")
    assert out["source_type"] == "rss"
    assert out["reachable"] is True


def test_reachability_is_reported_for_every_result_shape(adapters):
    """No branch may omit the key — a caller reading `detection["reachable"]`
    must never KeyError, and a caller using `.get()` must never silently read a
    missing key as falsy and report a healthy source as unreachable."""
    rss, page = adapters
    for rss_result, page_result in (
        ((None, [], True), ([], True)),
        ((None, [], True), ([], False)),
        ((None, [], False), ([], True)),
        (("https://f", [{"title": "x"}], True), ([], True)),
    ):
        rss.result = rss_result
        page.result = page_result
        assert "reachable" in detect_signal_source("https://example.com")


# --- LUM-843: the host that never answered ----------------------------------


def test_an_unreachable_host_short_circuits_before_the_page_fetch(adapters):
    """The RSS path's first bounded fetch already asked this exact URL.

    Handing the same dead address to the page scraper buys one more timeout and
    the same answer. Detection used to do exactly that — up to eleven sequential
    attempts on one click, two of them unbounded.
    """
    rss, page = adapters
    rss.result = (None, [], False)
    out = detect_signal_source("https://nope.invalid")
    assert out["reachable"] is False
    assert out["source_type"] == "page"
    assert page.calls == 0, "the page scraper must not be asked about a host that did not answer"


def test_reachability_comes_from_the_first_contact_not_the_last(adapters):
    """A site that answers early and rate-limits the tail is REACHABLE.

    Reachability used to be decided by the last of the ladder's fetches, so a
    host that served several probes and then 429'd the final one was reported
    as unreachable — and the user was told to check for a typo about an address
    that plainly worked.
    """
    rss, page = adapters
    rss.result = (None, [], True)  # answered the first contact
    page.result = ([], True)
    out = detect_signal_source("https://example.com")
    assert out["reachable"] is True
    assert page.calls == 1, "a reachable host still gets the page attempt"
