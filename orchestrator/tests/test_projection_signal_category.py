# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Guard test: project_signal carries `category` onto shared/system rows (LUM-632).

`GET /signals` resolves a signal's category slug to a label via the OWNER's
taxonomy. That is a no-op unless the *shared projection* of an email signal
actually carries the slug. Before LUM-632, ``project_signal``'s INSERT omitted
``category`` entirely, so shared rows landed with ``category=''`` and the whole
feature would silently do nothing.

This test drives the real ``project_signal`` with a fake metadata store that
parses its INSERT column list and zips it against the params (emulating
``RETURNING *``), then asserts ``category`` round-trips onto the shared row.
It also asserts ``source_url``/``source_label`` round-trip unchanged — a
positional mis-slot in the column/VALUES/params triple (all adjacent ``TEXT``)
would not raise a count error, so a multi-field assertion is what catches it.
"""

from __future__ import annotations

import re

import pytest
from auth import UserContext

import config
from services import projection


class _CapturingStore:
    """fetch_one parses the INSERT column list and returns a RETURNING-* row."""

    def __init__(self):
        self.captured: dict | None = None

    def fetch_one(self, sql, params=None):
        m = re.search(r"insert\s+into\s+signals\s*\(([^)]*)\)", sql, re.IGNORECASE)
        assert m, "could not parse INSERT column list"
        cols = [c.strip() for c in m.group(1).split(",")]
        params = tuple(params or ())
        assert len(cols) == len(params), (
            f"column/param count mismatch: {len(cols)} cols vs {len(params)} params"
        )
        row = dict(zip(cols, params))
        self.captured = row
        return row

    def fetch_all(self, sql, params=None):
        return []


@pytest.fixture
def store(monkeypatch):
    s = _CapturingStore()
    config._instances["metadata_store"] = s
    # Skip embedding + Qdrant entirely (vector=None → upsert is a no-op).
    monkeypatch.setattr(projection, "_embed_for_projection", lambda text: None)
    yield s
    config._instances.pop("metadata_store", None)


def test_project_signal_carries_category(store):
    src = {
        "signal_id": "11111111-1111-1111-1111-111111111111",
        "user_id": "A",
        "source_id": "src-1",
        "title": "Rent due",
        "url": "https://mail.example/x",
        "content_summary": "Your rent is due",
        "entities": [],
        "topics": [],
        "importance_score": 0.7,
        "relevance_score": 0.6,
        "notified": False,
        "category": "a_landlord",  # A's custom slug
        "source_url": "imap://mailbox/INBOX",
        "source_label": "Landlord mailbox",
    }
    actor = UserContext(user_id="A", is_authenticated=True, role="user", allows_shared=True)

    row = projection.project_signal(src, target_scope="shared", actor=actor)

    # The slug must survive onto the shared projection row.
    assert row["category"] == "a_landlord"
    assert store.captured["category"] == "a_landlord"
    # Adjacent same-TEXT columns must not be mis-slotted (positional guard).
    assert store.captured["source_url"] == "imap://mailbox/INBOX"
    assert store.captured["source_label"] == "Landlord mailbox"
    assert store.captured["scope"] == "shared"


def test_project_signal_empty_category_defaults_to_blank(store):
    # A non-email / unclassified signal (no category key) projects as '' — not
    # an error, and not a mis-slotted neighbour value.
    src = {
        "signal_id": "22222222-2222-2222-2222-222222222222",
        "user_id": "A",
        "source_id": "src-2",
        "title": "RSS item",
        "url": "https://feed.example/y",
        "source_url": "https://feed.example/rss",
        "source_label": "Feed",
    }
    actor = UserContext(user_id="A", is_authenticated=True, role="user", allows_shared=True)

    row = projection.project_signal(src, target_scope="shared", actor=actor)
    assert row["category"] == ""
    assert store.captured["source_label"] == "Feed"
