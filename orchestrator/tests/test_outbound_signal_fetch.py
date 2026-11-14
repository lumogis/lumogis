# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Outbound policy for the RSS / web-page source class (LUM-838).

This class had no SSRF guard at all — not at detection, not at poll — while
connectors got `validate_outbound_connector_base_url` and IMAP got
`_validate_imap_host` plus a Squid tunnel. The gap was never argued for; it was
never looked at.

These tests are mostly **negative**: what must be refused, and — just as
important — what must still be allowed, because a guard that blocks a legitimate
LAN feed in a self-hosted product would be reverted within a week and take the
real protection with it.

The redirect cases matter most. Validating an entry URL while following
redirects blindly is a partial fix that reads like a complete one.
"""

from __future__ import annotations

import httpx
import pytest
from services.outbound_signal_fetch import OutboundURLBlocked
from services.outbound_signal_fetch import fetch_text

from services import outbound_signal_fetch


class _Resp:
    def __init__(self, status=200, text="body", location=None):
        self.status_code = status
        self.text = text
        self.headers = {"location": location} if location else {}

    @property
    def is_redirect(self) -> bool:
        return self.status_code in (301, 302, 303, 307, 308) and "location" in self.headers


def _router(responses: dict[str, _Resp]):
    """Serve canned responses and record every URL actually fetched."""
    seen: list[str] = []

    def _get(url, **kwargs):
        seen.append(url)
        assert kwargs.get("follow_redirects") is False, (
            "redirects must be followed manually so each hop can be validated"
        )
        if url not in responses:
            raise httpx.ConnectError("no route")
        return responses[url]

    return _get, seen


@pytest.fixture
def net(monkeypatch):
    def _install(responses):
        get, seen = _router(responses)
        monkeypatch.setattr(httpx, "get", get)
        return seen

    return _install


# --- what must be refused ---------------------------------------------------


@pytest.mark.parametrize(
    "url,why",
    [
        ("file:///etc/passwd", "non-http scheme"),
        ("ftp://example.com/feed.xml", "non-http scheme"),
        ("http://user:pw@example.com/feed", "credentials in the authority"),
        ("", "empty"),
        ("  https://example.com  ", "surrounding whitespace"),
    ],
)
def test_refused_urls_raise_rather_than_returning_none(url, why, net):
    """A refusal is a decision the product can explain; `None` means "did not
    answer". Collapsing them is how a blocked URL gets reported as "no feed
    found" and the user is told nothing they can act on."""
    seen = net({})
    with pytest.raises(OutboundURLBlocked):
        fetch_text(url, timeout=5)
    assert seen == [], f"{why}: must be refused before any request is made"


def test_a_metadata_address_is_refused_when_the_operator_turns_private_off(net, monkeypatch):
    monkeypatch.setenv("LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS", "false")
    seen = net({})
    with pytest.raises(OutboundURLBlocked):
        fetch_text("http://169.254.169.254/latest/meta-data/", timeout=5)
    assert seen == []


# --- what must still be allowed --------------------------------------------


def test_a_lan_feed_is_allowed_by_default(net):
    """`LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS` defaults to true and this class
    keeps that shared default deliberately: watching an internal wiki or a LAN
    service's feed is ordinary in a self-hosted product, and a per-class
    divergence would be a fourth policy to reason about. The delta this guard
    closes is schemes, credentials and redirect hops — not LAN reach."""
    seen = net({"http://nas.lan/feed.xml": _Resp(text="<rss/>")})
    assert fetch_text("http://nas.lan/feed.xml", timeout=5) == "<rss/>"
    assert seen == ["http://nas.lan/feed.xml"]


def test_a_normal_fetch_returns_the_body(net):
    seen = net({"https://example.com/feed": _Resp(text="<rss>hi</rss>")})
    assert fetch_text("https://example.com/feed", timeout=5) == "<rss>hi</rss>"
    assert len(seen) == 1


# --- redirects: the half that a naive entry-only check misses ---------------


def test_a_redirect_to_a_blocked_target_is_refused(net):
    """The case entry-only validation cannot see.

    `https://harmless.example/r` passes any check you make at the door; the
    `Location` it returns is the request that actually matters.
    """
    seen = net(
        {
            "https://harmless.example/r": _Resp(302, location="http://169.254.169.254/"),
        }
    )
    with pytest.raises(OutboundURLBlocked):
        fetch_text("https://harmless.example/r", timeout=5)
    # The first hop was fetched — that is how we learned about the second — but
    # the blocked target never was.
    assert seen == ["https://harmless.example/r"]


def test_a_redirect_to_a_blocked_scheme_is_refused(net):
    seen = net({"https://harmless.example/r": _Resp(302, location="file:///etc/passwd")})
    with pytest.raises(OutboundURLBlocked):
        fetch_text("https://harmless.example/r", timeout=5)
    assert "file:///etc/passwd" not in seen


def test_an_ordinary_redirect_is_still_followed(net):
    """http→https upgrades and moved domains are normal for feeds. A guard that
    refused them would break real sources and buy nothing."""
    seen = net(
        {
            "http://example.com/feed": _Resp(301, location="https://example.com/feed"),
            "https://example.com/feed": _Resp(text="<rss/>"),
        }
    )
    assert fetch_text("http://example.com/feed", timeout=5) == "<rss/>"
    assert seen == ["http://example.com/feed", "https://example.com/feed"]


def test_a_relative_redirect_resolves_against_the_hop_it_came_from(net):
    """Relative `Location` headers are legal and common. Resolving them wrongly
    would either break real feeds or, worse, validate a different URL from the
    one fetched."""
    seen = net(
        {
            "https://example.com/old/feed": _Resp(302, location="/new/feed"),
            "https://example.com/new/feed": _Resp(text="<rss/>"),
        }
    )
    assert fetch_text("https://example.com/old/feed", timeout=5) == "<rss/>"
    assert seen[-1] == "https://example.com/new/feed"


def test_a_redirect_loop_terminates(net):
    seen = net({"https://example.com/a": _Resp(302, location="https://example.com/a")})
    assert fetch_text("https://example.com/a", timeout=5) is None
    assert len(seen) <= outbound_signal_fetch._MAX_REDIRECTS + 1


# --- non-2xx and transport failures are "did not answer", not "refused" -----


def test_a_404_is_not_an_answer_but_is_not_a_refusal_either(net):
    seen = net({"https://example.com/feed": _Resp(404, text="nope")})
    assert fetch_text("https://example.com/feed", timeout=5) is None
    assert seen == ["https://example.com/feed"]


def test_a_connection_error_returns_none(net):
    net({})
    assert fetch_text("https://unreachable.example/feed", timeout=5) is None


# --- the refusal must be legible at the route -------------------------------


def test_the_route_refuses_a_blocked_url_with_a_reason(monkeypatch):
    """`POST /sources` must say *why*, not fail downstream as "no feed found".

    A refused URL that surfaces as a generic detection miss tells the user
    nothing they can act on — they retype the same address and get the same
    nothing. This is the difference between a guard and a wall.
    """
    from contextlib import contextmanager

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)

    @contextmanager
    def _client():
        import main
        from fastapi.testclient import TestClient

        with TestClient(main.app) as c:
            yield c

    called: list[str] = []
    import routes.signals as signals_routes

    monkeypatch.setattr(signals_routes, "_detect_source", lambda u: called.append(u))

    with _client() as client:
        resp = client.post("/sources", json={"url": "file:///etc/passwd", "confirm": False})

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "source_url_not_allowed"
    assert "scheme" in resp.json()["detail"]["message"].lower()
    assert called == [], "detection must not run on a URL policy already refused"
