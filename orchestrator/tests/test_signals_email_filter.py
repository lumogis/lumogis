# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-642 — GET /signals ``?source_type=imap`` email-only scoping.

The /email surface must show ONLY email signals, but still over the household
union (so a member sees another member's *shared* email) — not narrowed to the
viewer's own mailboxes. The filter is a ``url LIKE 'imap://%'`` condition
appended to the existing ``visible_filter`` union. This suite proves list_signals
passes that condition (the fake store honours it) and that the result includes a
cross-user shared imap signal while excluding RSS/system rows.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

import pytest
from auth import UserContext
from routes.signals import list_signals

import config


class FilterAwareStore:
    """In-memory store that honours the union + the ``url LIKE 'imap://%'`` filter."""

    def __init__(self):
        self.signals: list[dict] = []
        self._seq = 0

    def add(self, *, user_id, scope, url, signal_id=None, from_addr="", from_display=""):
        self._seq += 1
        self.signals.append(
            {
                "signal_id": signal_id or f"sig-{self._seq}",
                "user_id": user_id,
                "source_id": f"src-{self._seq}",
                "title": f"Title {self._seq}",
                "url": url,
                "published_at": datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc),
                "content_summary": "",
                "entities": [],
                "topics": [],
                "importance_score": 0.5,
                "relevance_score": 0.5,
                "notified": False,
                "created_at": datetime(2026, 7, 20, 10, self._seq % 60, tzinfo=timezone.utc),
                "category": "",
                "from_addr": from_addr,
                "from_display": from_display,
                "scope": scope,
                "source_url": None,
                "source_label": None,
            }
        )

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def fetch_one(self, query, params=None):
        return None

    def fetch_all(self, query, params=None):
        q = query.lower()
        p = params or ()
        if "from signals" in q:
            me = p[0]
            limit = p[-1]
            rows = [
                r
                for r in self.signals
                if (r["scope"] == "personal" and r["user_id"] == me)
                or r["scope"] in ("shared", "system")
            ]
            # Honour the LUM-642 email filter exactly as Postgres would.
            if "url like" in q:
                rows = [r for r in rows if r["url"].startswith("imap://")]
            rows.sort(key=lambda r: r["created_at"], reverse=True)
            return rows[:limit]
        # taxonomy / rule lookups from label resolution — no-op.
        return []


@pytest.fixture
def store():
    s = FilterAwareStore()
    config._instances["metadata_store"] = s
    config.invalidate_email_taxonomy_cache()
    yield s
    config._instances.pop("metadata_store", None)
    config.invalidate_email_taxonomy_cache()


def _viewer(user_id: str) -> UserContext:
    return UserContext(user_id=user_id, is_authenticated=True, role="user", allows_shared=True)


def _list(user_id: str, source_type=None) -> list[dict]:
    resp = list_signals(
        topic=None,
        entity=None,
        source_id=None,
        source_type=source_type,
        min_relevance_score=None,
        scope=None,
        limit=50,
        user=_viewer(user_id),
    )
    return resp["signals"]


def test_source_type_imap_returns_only_email_including_shared(store):
    # Alice's own email, an RSS headline, a system signal, and BOB's SHARED email.
    store.add(user_id="A", scope="personal", url="imap://srcA/1/9", signal_id="mine")
    store.add(user_id="A", scope="personal", url="https://news.test/x", signal_id="rss")
    store.add(user_id="__system__", scope="system", url="system://health", signal_id="sys")
    store.add(
        user_id="B",
        scope="shared",
        url="imap://srcB/2/7",
        signal_id="bob-shared",
        from_addr="bob@ex.com",
        from_display="Bob Example",
    )

    ids = {r["signal_id"] for r in _list("A", source_type="imap")}
    assert ids == {"mine", "bob-shared"}  # email only, incl. another member's shared email
    assert "rss" not in ids and "sys" not in ids


def test_no_source_type_returns_the_full_union(store):
    # Without the filter, the page-agnostic union still returns everything visible.
    store.add(user_id="A", scope="personal", url="imap://srcA/1/9", signal_id="mine")
    store.add(user_id="A", scope="personal", url="https://news.test/x", signal_id="rss")
    ids = {r["signal_id"] for r in _list("A")}
    assert ids == {"mine", "rss"}


def test_shared_email_row_carries_sender(store):
    store.add(
        user_id="B",
        scope="shared",
        url="imap://srcB/2/7",
        signal_id="bob-shared",
        from_addr="bob@ex.com",
        from_display="Bob Example",
    )
    row = next(r for r in _list("A", source_type="imap") if r["signal_id"] == "bob-shared")
    assert row["from_addr"] == "bob@ex.com"
    assert row["from_display"] == "Bob Example"
