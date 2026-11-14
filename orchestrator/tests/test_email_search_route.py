# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""HTTP contract for ``GET /api/v1/email/search`` (LUM-819).

The status codes carry decisions, not just plumbing:

* **400, not an empty list**, for an unusable query. A punctuation-only query
  matches nothing *without raising*, so returning ``[]`` would be a silent
  failure — the exact thing this feature exists to remove.
* **404, not 403**, for a mailbox the caller does not own. A 403 confirms the
  source exists and turns the gate into an oracle over other users' mailboxes.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from routes.api_v1 import email_search as route_mod

import config


class _Store:
    def __init__(self, rows=None, counts=None):
        self._rows = rows or []
        self._counts = counts or {}

    def fetch_all(self, sql, params=None):
        return list(self._rows)

    def fetch_one(self, sql, params=None):
        if "WITH m" in sql:
            return dict(self._counts)
        return {"oldest": None}

    def execute(self, sql, params=None):
        return None


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store())
    route_mod._limiter.check.__self__._store.clear()  # isolate the rate bucket
    from main import app

    return TestClient(app)


def test_search_returns_200_with_counts(client, monkeypatch):
    rows = [
        {
            "signal_id": "s1",
            "title": "Plumbing invoice",
            "from_addr": "office@plumbers.example",
            "from_display": "Plumbers Ltd",
            "content_summary": "",
            "published_from": None,
            "triage_status": None,
            "body_text": None,
            "body_truncated": False,
            "body_matched": False,
        }
    ]
    monkeypatch.setattr(
        config,
        "get_metadata_store",
        lambda: _Store(rows, {"all_n": 1, "subject_n": 1, "sender_n": 0, "body_n": 0}),
    )

    r = client.get("/api/v1/email/search", params={"q": "plumbing"})

    assert r.status_code == 200
    body = r.json()
    assert body["counts"]["subject"] == 1
    assert body["fields"] == "all"
    assert body["hits"][0]["matched_in"] == ["subject"]


@pytest.mark.parametrize("bad", ["ab", "", "!!!!"])
def test_an_unusable_query_is_400_not_an_empty_list(client, bad):
    r = client.get("/api/v1/email/search", params={"q": bad})
    assert r.status_code in (400, 422), f"{bad!r} should be refused, got {r.status_code}"


def test_an_unknown_fields_value_is_422(client):
    r = client.get("/api/v1/email/search", params={"q": "invoice", "fields": "everything"})
    assert r.status_code == 422


def test_a_foreign_source_id_is_404_not_403(client, monkeypatch):
    from actions.errors import PermanentActionError

    def _deny(source_id, user_id):
        raise PermanentActionError("not yours")

    monkeypatch.setattr("actions.handlers.imap_writeback._load_owned_source", _deny)

    r = client.get("/api/v1/email/search", params={"q": "invoice", "source_id": "someone-elses"})

    assert r.status_code == 404, "403 would confirm the mailbox exists"


def test_limit_out_of_range_is_422(client):
    r = client.get("/api/v1/email/search", params={"q": "invoice", "limit": 5000})
    assert r.status_code == 422


def test_rate_limited_requests_get_429(client):
    for _ in range(30):
        client.get("/api/v1/email/search", params={"q": "invoice"})

    r = client.get("/api/v1/email/search", params={"q": "invoice"})

    assert r.status_code == 429


def test_hits_carry_the_fields_collapse_projections_needs(client, monkeypatch):
    """Without these the web-side dedupe silently no-ops.

    ``collapseProjections`` groups on ``published_from ?? signal_id`` and
    tie-breaks on ``triage_status``. A hit model missing them would let an
    author see their own shared mail twice, with a green suite.
    """
    rows = [
        {
            "signal_id": "s1",
            "title": "Invoice",
            "from_addr": "a@b.example",
            "from_display": "",
            "content_summary": "",
            "published_from": None,
            "triage_status": "done",
            "body_text": None,
            "body_truncated": False,
            "body_matched": False,
        }
    ]
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store(rows, {}))

    hit = client.get("/api/v1/email/search", params={"q": "invoice"}).json()["hits"][0]

    assert "published_from" in hit
    assert "triage_status" in hit
