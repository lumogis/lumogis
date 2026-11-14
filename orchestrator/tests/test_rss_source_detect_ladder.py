# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""`RSSSource`'s feed-detection ladder — fetch count and bounding (LUM-843).

This code had **no tests at all** before now, and the reason is instructive:
`feedparser` is a Docker-only dependency, so importing the adapter fails in this
environment. Its absence is exactly why a ladder that could fire eleven
sequential requests — two of them with no timeout — went unnoticed.

`feedparser` is only ever imported *inside* the functions that use it, so a stub
in `sys.modules` is enough to make the whole path exercisable. Every network call
goes through `_fetch_text`, which these tests replace with a recorder: the
assertions are about **how many times we go to the network and with what
timeout**, which is the substance of the ticket.
"""

from __future__ import annotations

import sys
import types

import pytest
from models.signals import SourceConfig


class _StubFeed:
    def __init__(self, entries=None, title=None, bozo=0):
        self.entries = entries or []
        self.feed = {"title": title} if title else {}
        self.bozo = bozo


@pytest.fixture
def rss(monkeypatch):
    """Install a stub `feedparser` and hand back the adapter class.

    `parse` records what it was given so a test can prove it received a *body*
    and never a URL — the distinction the whole fix rests on, since
    `feedparser.parse(url)` performs its own unbounded fetch.
    """
    stub = types.ModuleType("feedparser")
    stub.parsed: list[str] = []
    stub.result_for = {}

    def _parse(text):
        stub.parsed.append(text)
        return stub.result_for.get(text, _StubFeed())

    stub.parse = _parse
    monkeypatch.setitem(sys.modules, "feedparser", stub)

    from adapters.rss_source import RSSSource

    return RSSSource, stub


def _config(url: str) -> SourceConfig:
    return SourceConfig(
        id="src-1",
        name="feed",
        source_type="rss",
        url=url,
        category="",
        active=True,
        poll_interval=3600,
        extraction_method="feedparser",
        css_selector_override=None,
        last_polled_at=None,
        last_signal_at=None,
    )


def _recorder(responses: dict[str, str | None]):
    """Replace `_fetch_text`, recording `(url, timeout)` for each call."""
    calls: list[tuple[str, float]] = []

    def _fetch(self, url, *, timeout):
        calls.append((url, timeout))
        return responses.get(url)

    return _fetch, calls


def test_an_unreachable_host_costs_exactly_one_fetch(rss, monkeypatch):
    """The headline of LUM-843.

    This used to be up to eleven: `feedparser.parse(base)` (unbounded), a page
    fetch, eight common-path probes, and a second unbounded parse.
    """
    RSSSource, _stub = rss
    fetch, calls = _recorder({})  # nothing answers
    monkeypatch.setattr(RSSSource, "_fetch_text", fetch)

    instance = RSSSource(_config("https://nope.invalid/blog"))
    assert instance._detect_feed_url() == "https://nope.invalid/blog"
    assert len(calls) == 1, f"expected one fetch, got {[c[0] for c in calls]}"
    assert instance.last_fetch_ok is False


def test_no_detection_fetch_is_unbounded(rss, monkeypatch):
    """Every call carries a timeout.

    `feedparser.parse(url)` fetches through urllib with the OS default socket
    timeout — minutes on a blackholed host — and two of those sat on this path.
    """
    RSSSource, _stub = rss
    fetch, calls = _recorder({"https://example.com": "<html></html>"})
    monkeypatch.setattr(RSSSource, "_fetch_text", fetch)

    RSSSource(_config("https://example.com"))._detect_feed_url()
    assert calls, "expected at least one fetch"
    for url, timeout in calls:
        assert isinstance(timeout, (int, float)) and timeout > 0, f"unbounded fetch: {url}"


def test_feedparser_is_never_handed_a_url(rss, monkeypatch):
    """It is handed bodies. A URL would mean an unbounded fetch inside it."""
    RSSSource, stub = rss
    fetch, _calls = _recorder({"https://example.com": "<html>no feed here</html>"})
    monkeypatch.setattr(RSSSource, "_fetch_text", fetch)

    RSSSource(_config("https://example.com"))._detect_feed_url()
    assert stub.parsed, "expected feedparser.parse to be called"
    for arg in stub.parsed:
        assert not arg.startswith("http"), f"parse() was given a URL, not a body: {arg[:60]}"


def test_the_base_url_is_fetched_once_not_twice(rss, monkeypatch):
    """One body answers both questions: is it a feed, and does it name one?

    The old ladder asked `feedparser.parse(base)` and then re-fetched the same
    address in `_detect_from_page_head`.
    """
    RSSSource, _stub = rss
    fetch, calls = _recorder({"https://example.com": "<html>nothing</html>"})
    monkeypatch.setattr(RSSSource, "_fetch_text", fetch)

    RSSSource(_config("https://example.com"))._detect_feed_url()
    base_fetches = [u for u, _ in calls if u == "https://example.com"]
    assert len(base_fetches) == 1, f"base fetched {len(base_fetches)} times"


def test_a_base_url_that_is_already_a_feed_stops_immediately(rss, monkeypatch):
    RSSSource, stub = rss
    body = "<rss><channel><title>t</title></channel></rss>"
    stub.result_for[body] = _StubFeed(entries=[{"title": "one"}])
    fetch, calls = _recorder({"https://example.com/feed.xml": body})
    monkeypatch.setattr(RSSSource, "_fetch_text", fetch)

    instance = RSSSource(_config("https://example.com/feed.xml"))
    assert instance._detect_feed_url() == "https://example.com/feed.xml"
    assert len(calls) == 1
    assert instance.last_fetch_ok is True


def test_a_link_tag_is_found_without_another_fetch_of_the_base(rss, monkeypatch):
    RSSSource, _stub = rss
    html = (
        '<html><head><link rel="alternate" type="application/rss+xml" href="/f.xml"></head></html>'
    )
    fetch, calls = _recorder({"https://example.com": html})
    monkeypatch.setattr(RSSSource, "_fetch_text", fetch)

    instance = RSSSource(_config("https://example.com"))
    assert instance._detect_feed_url() == "https://example.com/f.xml"
    assert len(calls) == 1, "the link tag came from the body we already had"


def test_common_paths_are_probed_only_when_the_host_answers(rss, monkeypatch):
    """A reachable host with no feed still earns the probe ladder — that is the
    case the ladder exists for. The point of the fix is that a *dead* host does
    not."""
    RSSSource, stub = rss
    feed_body = "<rss>ok</rss>"
    stub.result_for[feed_body] = _StubFeed(entries=[{"title": "x"}])
    fetch, calls = _recorder(
        {"https://example.com": "<html>nothing</html>", "https://example.com/feed": feed_body}
    )
    monkeypatch.setattr(RSSSource, "_fetch_text", fetch)

    instance = RSSSource(_config("https://example.com"))
    assert instance._detect_feed_url() == "https://example.com/feed"
    assert len(calls) > 1, "a reachable host should still be probed"
    assert instance.last_fetch_ok is True


def test_poll_reports_a_failed_fetch_without_raising(rss, monkeypatch):
    """`poll()` returns `[]` either way; only the flag distinguishes them, and
    `_poll_signal_source` records health from the flag."""
    RSSSource, _stub = rss
    fetch, _calls = _recorder({})
    monkeypatch.setattr(RSSSource, "_fetch_text", fetch)

    instance = RSSSource(_config("https://nope.invalid/feed"))
    assert instance.poll() == []
    assert instance.last_fetch_ok is False


def test_poll_of_a_reachable_but_empty_feed_stays_ok(rss, monkeypatch):
    """Same empty list, opposite health — the pair the flag exists for."""
    RSSSource, stub = rss
    body = "<rss></rss>"
    stub.result_for[body] = _StubFeed(entries=[])
    fetch, _calls = _recorder({"https://example.com/feed": body})
    monkeypatch.setattr(RSSSource, "_fetch_text", fetch)

    instance = RSSSource(_config("https://example.com/feed"))
    assert instance.poll() == []
    assert instance.last_fetch_ok is True
