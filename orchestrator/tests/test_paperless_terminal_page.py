# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The terminal page of a paperless walk is a 404, not an empty page (LUM-747).

``_poll_paperless_source`` stops when ``fetch_documents_page`` returns nothing,
and its only way to learn that is to ask for a page that does not exist. DRF's
``PageNumberPagination`` answers that with **404** — confirmed against live
paperless 3.0.4 in the LUM-309 Step 0 spike, where page 4 of 3 returned
``{"detail":"Invalid page."}``.

Before this fix a 404 was neither 401/403 nor 429/5xx, so it fell through to
``raise_for_status()``, became an ``httpx.HTTPStatusError``, and was retried by
the generic ``except httpx.HTTPError`` for all five attempts — **~31 seconds of
sleeping and a `paperless_http_final_failure` ERROR on every productive tick.**

**These tests use a mock transport, which is a belief about the server, not the
server.** That is precisely the gap LUM-309 exists to close, and it is why the
404 encoded here was *measured* first rather than assumed. Do not read this file
as making the live fixture unnecessary.

**The fix that is deliberately NOT here.** LUM-747 originally proposed an early
exit on ``len(docs) < page_size`` in the poll loop. That is unsafe:
``fetch_documents_page`` *filters* its output — blank ``content`` and missing
``added`` rows are dropped — so a full page containing three blank-content scans
returns 22 of 25 and would read as "last page", silently truncating the walk and
dropping every document after it. Blank content is what an image-only scan looks
like before OCR finishes, so the loss would concentrate on exactly the newest
documents. ``len(docs)`` is not a pagination signal; if a future change wants to
avoid the wasted request, read ``next`` from the response envelope.
"""

from __future__ import annotations

import logging
import time

import httpx
import pytest
from adapters.paperless_source import PaperlessPoller
from services.paperless_credentials import PaperlessConnection

_BASE = "https://paperless.example"


@pytest.fixture
def patched_client(monkeypatch):
    """Route every httpx.Client built inside the adapter through a mock transport."""

    def _install(handler):
        original = httpx.Client

        def _factory(*args, **kwargs):
            kwargs.setdefault("transport", httpx.MockTransport(handler))
            return original(*args, **kwargs)

        monkeypatch.setattr("adapters.paperless_source.httpx.Client", _factory)
        return PaperlessPoller(PaperlessConnection(base_url=_BASE, token="t0ken"))

    return _install


def test_a_page_past_the_end_returns_an_empty_page_not_an_error(patched_client):
    poller = patched_client(lambda _r: httpx.Response(404, json={"detail": "Invalid page."}))

    assert poller.fetch_documents_page(since_cursor=None, page=4) == ([], False)


def test_a_page_past_the_end_returns_immediately_without_retrying(patched_client):
    """The test that pins the actual defect.

    The assertion above passes even with the retry ladder fully intact — it just
    takes 31 seconds to do it. Only the call count and the clock catch the bug.
    """
    calls = {"n": 0}

    def _handler(_request):
        calls["n"] += 1
        return httpx.Response(404, json={"detail": "Invalid page."})

    poller = patched_client(_handler)
    started = time.monotonic()
    poller.fetch_documents_page(since_cursor=None, page=4)
    elapsed = time.monotonic() - started

    assert calls["n"] == 1, f"a 404 must not be retried; made {calls['n']} requests"
    assert elapsed < 1.0, f"a 404 must not sleep; took {elapsed:.1f}s"


def test_a_404_does_not_log_at_error(patched_client, caplog):
    """The ERROR is half the reported harm — a healthy tick must not emit one."""
    poller = patched_client(lambda _r: httpx.Response(404, json={"detail": "Invalid page."}))

    with caplog.at_level(logging.INFO, logger="adapters.paperless_source"):
        poller.fetch_documents_page(since_cursor=None, page=4)

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, f"a page past the end is not an error: {[r.getMessage() for r in errors]}"
    assert any("paperless_page_past_end" in r.getMessage() for r in caplog.records)


def test_a_404_on_page_one_warns_because_that_is_a_broken_endpoint(patched_client, caplog):
    """Page 1 is not "past the end" — it is a misconfiguration.

    DRF's ``allow_empty_first_page`` defaults True, so an empty archive answers
    page 1 with a 200 and ``count: 0``. A 404 there means the base URL is wrong.

    Found in code review of the LUM-747 fix, which as first written logged every
    404 at INFO — silently downgrading a permanently-broken connector from a
    per-tick ERROR to a line nobody reads, while it returned "no documents"
    forever. The latency fix must not cost the diagnosis.
    """
    poller = patched_client(lambda _r: httpx.Response(404, json={"detail": "Not found."}))

    with caplog.at_level(logging.INFO, logger="adapters.paperless_source"):
        docs, auth_fail = poller.fetch_documents_page(since_cursor=None, page=1)

    assert (docs, auth_fail) == ([], False), "still terminal — the walk must not hang or raise"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("paperless_documents_endpoint_not_found" in r.getMessage() for r in warnings), (
        f"a 404 on page 1 must be visible, got: {[r.getMessage() for r in caplog.records]}"
    )
    assert not any("paperless_page_past_end" in r.getMessage() for r in caplog.records)


def test_a_500_still_retries(patched_client, monkeypatch):
    """Guards against over-broadening the fix into 'never retry anything'."""
    monkeypatch.setattr("adapters.paperless_source._BASE_DELAY_S", 0.0)
    calls = {"n": 0}

    def _handler(_request):
        calls["n"] += 1
        return httpx.Response(500, text="upstream")

    poller = patched_client(_handler)
    poller.fetch_documents_page(since_cursor=None, page=1)

    from adapters.paperless_source import _MAX_ATTEMPTS

    assert calls["n"] == _MAX_ATTEMPTS, "5xx must still exhaust the retry ladder"


def test_a_401_still_reports_auth_failure(patched_client):
    """The 404 branch sits beside the auth branch.

    An ordering mistake between them would disable auth detection silently, and
    a poller that never reports auth failure fails open forever.
    """
    poller = patched_client(lambda _r: httpx.Response(401, json={"detail": "bad token"}))

    assert poller.fetch_documents_page(since_cursor=None, page=1) == ([], True)


def test_a_good_page_is_still_parsed(patched_client):
    """The sentinel must not shadow the happy path."""
    body = {
        "count": 1,
        "next": None,
        "results": [
            {"id": 7, "content": "an invoice", "added": "2026-07-30T10:00:00Z", "title": "Bill"}
        ],
    }
    poller = patched_client(lambda _r: httpx.Response(200, json=body))

    docs, auth_fail = poller.fetch_documents_page(since_cursor=None, page=1)

    assert auth_fail is False
    assert [(d.id, d.title) for d in docs] == [(7, "Bill")]
