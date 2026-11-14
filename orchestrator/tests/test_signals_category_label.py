# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route tests for cross-user slug->label resolution on GET /signals (LUM-632).

`signals.category` is a slug resolved to a human label via a *user's* taxonomy
(LUM-629). `GET /signals` returns the household union, so a member sees another
member's *shared* email signals carrying slugs from the OWNER's taxonomy. This
suite exercises the enrichment that resolves each row's ``category_label`` via
the signal owner's (`signals.user_id`) taxonomy, batched per distinct owner.

Uses a functional in-memory fake ``MetadataStore`` that (a) emulates the
``visible_filter`` union (not return-all) for the ``list_signals`` SELECT,
(b) answers each owner's ``email_category_taxonomy`` SELECT, and (c) no-ops the
``email_classification_rule`` SELECT (returns ``[]`` — never raises) that
``_build_cache`` issues alongside the taxonomy query. Shared fixtures are built
via the ``project_signal`` column contract (they carry ``category``), so the
suite cannot pass against the pre-LUM-632 projection INSERT.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

import pytest
from auth import UserContext
from routes.signals import list_signals

import config


class FakeSignalsStore:
    """In-memory store: signals union query + per-owner taxonomy query."""

    def __init__(self):
        self.signals: list[dict] = []
        # user_id -> list of taxonomy override rows {slug,label,enabled,sort}
        self.tax: dict[str, list[dict]] = {}
        self.fail_taxonomy_for: set[str] = set()
        self._seq = 0

    # -- helpers -------------------------------------------------------------
    def add_signal(self, *, user_id, category, scope, signal_id=None, source_id="src1"):
        """Add a signal row shaped like the project_signal / signals contract."""
        self._seq += 1
        self.signals.append(
            {
                "signal_id": signal_id or f"sig-{self._seq}",
                "user_id": user_id,
                "source_id": source_id,
                "title": f"Title {self._seq}",
                "url": "https://example.test/x",
                "published_at": datetime(2026, 7, 18, 9, 0, tzinfo=timezone.utc),
                "content_summary": "",
                "entities": [],
                "topics": [],
                "importance_score": 0.5,
                "relevance_score": 0.5,
                "notified": False,
                "created_at": datetime(2026, 7, 18, 10, self._seq % 60, tzinfo=timezone.utc),
                "category": category,
                "scope": scope,
                "source_url": None,
                "source_label": None,
            }
        )

    def set_custom_label(self, user_id, slug, label, sort=10):
        self.tax.setdefault(user_id, []).append(
            {"slug": slug, "label": label, "enabled": True, "sort": sort}
        )

    # -- MetadataStore surface ----------------------------------------------
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
            # Faithful visible_filter union (default scope): personal-mine +
            # shared/system. NOT return-all.
            visible = [
                r
                for r in self.signals
                if (r["scope"] == "personal" and r["user_id"] == me)
                or r["scope"] in ("shared", "system")
            ]
            visible.sort(key=lambda r: r["created_at"], reverse=True)
            return visible[:limit]
        if "from email_category_taxonomy" in q:
            uid = p[0]
            if uid in self.fail_taxonomy_for:
                raise RuntimeError("simulated taxonomy load failure")
            return list(self.tax.get(uid, []))
        if "from email_classification_rule" in q:
            # (c) label_map's cold cache also issues this — no-op, never raise.
            return []
        return []


@pytest.fixture
def store():
    s = FakeSignalsStore()
    config._instances["metadata_store"] = s
    config.invalidate_email_taxonomy_cache()
    yield s
    config._instances.pop("metadata_store", None)
    config.invalidate_email_taxonomy_cache()


def _viewer(user_id: str) -> UserContext:
    # Authenticated member with shared access (avoids the users-table lookup in
    # _effective_allows_shared); household union applies.
    return UserContext(user_id=user_id, is_authenticated=True, role="user", allows_shared=True)


def _list(user_id: str) -> list[dict]:
    resp = list_signals(
        topic=None,
        entity=None,
        source_id=None,
        min_relevance_score=None,
        scope=None,
        limit=20,
        user=_viewer(user_id),
    )
    return resp["signals"]


def _by_id(rows, signal_id):
    return next(r for r in rows if r["signal_id"] == signal_id)


# --------------------------------------------------------------------------- #
# Cross-user resolution
# --------------------------------------------------------------------------- #
def test_shared_signal_resolves_via_owner_taxonomy(store):
    # A owns a shared signal tagged with A's custom slug 'a_landlord'=Landlord.
    store.set_custom_label("A", "a_landlord", "Landlord")
    store.add_signal(user_id="A", category="a_landlord", scope="shared", signal_id="s-shared")

    rows = _list("B")  # viewer B (different user) sees the household union
    row = _by_id(rows, "s-shared")
    assert row["category"] == "a_landlord"  # slug unchanged
    assert row["category_label"] == "Landlord"  # resolved via OWNER A's taxonomy
    assert "user_id" not in row  # owner id never serialized


def test_own_personal_signal_resolves_via_own_taxonomy(store):
    # B's own personal signal with a default slug resolves via B's taxonomy.
    store.add_signal(user_id="B", category="school", scope="personal", signal_id="s-own")
    rows = _list("B")
    row = _by_id(rows, "s-own")
    assert row["category_label"] == "School"  # default label


def test_unresolvable_slug_falls_back_to_raw_slug(store):
    # A shared signal whose slug is not in the owner's taxonomy (deleted custom).
    store.add_signal(user_id="A", category="gone_custom", scope="shared", signal_id="s-gone")
    rows = _list("B")
    row = _by_id(rows, "s-gone")
    assert row["category"] == "gone_custom"
    assert row["category_label"] == "gone_custom"  # raw-slug fallback, never blank


def test_empty_category_yields_empty_label(store):
    store.add_signal(user_id="A", category="", scope="shared", signal_id="s-empty")
    rows = _list("B")
    row = _by_id(rows, "s-empty")
    assert row["category"] == ""
    assert row["category_label"] == ""


def test_general_is_a_normal_slug_not_special_cased(store):
    # 'general' is a legal custom slug (not sources.category); it must resolve
    # normally — raw slug when the owner has no such taxonomy entry, NOT "".
    store.add_signal(user_id="A", category="general", scope="shared", signal_id="s-general")
    rows = _list("B")
    row = _by_id(rows, "s-general")
    assert row["category"] == "general"
    assert row["category_label"] == "general"  # never collapsed to ""


def test_owner_taxonomy_load_error_degrades_to_raw_slug(store):
    store.set_custom_label("A", "a_landlord", "Landlord")
    store.add_signal(user_id="A", category="a_landlord", scope="shared", signal_id="s-err")
    store.fail_taxonomy_for.add("A")  # A's taxonomy load raises

    rows = _list("B")  # list must still return 200/rows
    row = _by_id(rows, "s-err")
    assert row["category"] == "a_landlord"
    assert row["category_label"] == "a_landlord"  # degraded to raw slug, no exception


def test_two_owners_no_cross_contamination(store):
    # A and B both have a custom slug; each resolves via its own owner map.
    store.set_custom_label("A", "a_landlord", "A-Landlord")
    store.set_custom_label("C", "a_landlord", "C-Landlord")
    store.add_signal(user_id="A", category="a_landlord", scope="shared", signal_id="s-a")
    store.add_signal(user_id="C", category="a_landlord", scope="shared", signal_id="s-c")

    rows = _list("B")
    assert _by_id(rows, "s-a")["category_label"] == "A-Landlord"
    assert _by_id(rows, "s-c")["category_label"] == "C-Landlord"
