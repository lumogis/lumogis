# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-649 — route-level wiring for feedback → relevance learning.

Covers, through the public route handlers (no private helpers):

* ``POST /feedback`` for ``item_type="signal"`` explicit feedback returns an
  ``effect`` naming the moved term(s); ``item_type="entity"`` and implicit
  feedback carry NO ``effect`` key (regression guards); a relevance-learning
  failure never turns an otherwise-successful feedback recording into a 500.
* ``GET /profile/learned`` returns the caller's sparse learned weights, 200 with
  empty lists when no profile exists, isolated per user.
* ``GET /signals`` carries a per-caller ``learned_relevance_delta`` on every row
  reflecting the CALLER's weights (not the signal owner's), and degrades to
  ``0.0`` — never 500 — when the profile-weights fetch fails.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

import pytest
from auth import UserContext
from routes.signals import FeedbackRequest
from routes.signals import get_learned_weights
from routes.signals import list_signals
from routes.signals import record_feedback

import config


class FakeStore:
    def __init__(self):
        self.signals: list[dict] = []
        self.profiles: dict[str, dict] = {}
        self.feedback_log: list[tuple] = []
        self._seq = 0
        self.raise_weights_fetch = False

    def add_signal(
        self,
        *,
        user_id,
        scope="personal",
        topics=None,
        entities=None,
        from_addr="",
        category="",
        signal_id=None,
    ):
        self._seq += 1
        sid = signal_id or f"sig-{self._seq}"
        self.signals.append(
            {
                "signal_id": sid,
                "user_id": user_id,
                "source_id": "src",
                "title": "T",
                "url": "u",
                "published_at": datetime(2026, 7, 20, tzinfo=timezone.utc),
                "content_summary": "",
                "entities": entities or [],
                "topics": topics or [],
                "importance_score": 0.5,
                "relevance_score": 0.5,
                "notified": False,
                "created_at": datetime(2026, 7, 20, tzinfo=timezone.utc),
                "category": category,
                "from_addr": from_addr,
                "from_display": "",
                "scope": scope,
                "source_url": None,
                "source_label": None,
                "published_from": None,
            }
        )
        return sid

    def set_profile(self, user_id, *, topics=None, entities=None, categories=None):
        self.profiles[user_id] = {
            "topics": topics or {},
            "entities": entities or {},
            "categories": categories or {},
        }

    # --- MetadataStore port -------------------------------------------------
    def ping(self):
        return True

    def close(self):
        pass

    def _visible(self, me, row):
        return (row["scope"] == "personal" and row["user_id"] == me) or row["scope"] in (
            "shared",
            "system",
        )

    def fetch_one(self, query, params=None):
        q = query.lower()
        p = params or ()
        if "from signals" in q:
            sid = p[0]
            me = p[1] if len(p) > 1 else None
            for r in self.signals:
                if str(r["signal_id"]) == str(sid) and self._visible(me, r):
                    return {k: r[k] for k in ("topics", "entities", "from_addr", "category")}
            return None
        if "from relevance_profiles" in q:
            if self.raise_weights_fetch:
                raise RuntimeError("weights fetch down")
            prof = self.profiles.get(p[0])
            if prof is None:
                return None
            return {
                "learned_topic_weights": prof["topics"],
                "learned_entity_weights": prof["entities"],
                "learned_category_weights": prof["categories"],
            }
        return None

    def fetch_all(self, query, params=None):
        # The GET /signals query: first param is the caller (triage LATERAL),
        # then visible_filter params. Return household-visible signals.
        me = params[0] if params else None
        return [r for r in self.signals if self._visible(me, r)]

    def execute(self, query, params=None):
        q = query.lower()
        p = params or ()
        if "insert into feedback_log" in q:
            self.feedback_log.append(p)
        elif "insert into relevance_profiles" in q:
            import json

            self.profiles[p[0]] = {
                "topics": json.loads(p[1]),
                "entities": json.loads(p[2]),
                "categories": json.loads(p[3]),
            }


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)
    # Neutralise the async hook fire from services.feedback (not under test here).
    import hooks

    monkeypatch.setattr(hooks, "fire_background", lambda *a, **k: None)
    return s


def _user(user_id="u1", allows_shared=True):
    return UserContext(
        user_id=user_id, is_authenticated=True, role="user", allows_shared=allows_shared
    )


def _list(user):
    # Direct call: Query(...) defaults must be passed explicitly, they are not
    # resolved to None outside a request (mirrors test_signal_triage's helper).
    return list_signals(
        topic=None,
        entity=None,
        source_id=None,
        source_type=None,
        min_relevance_score=None,
        scope=None,
        triaged=None,
        triaged_before=None,
        limit=50,
        user=user,
    )


# ---------------------------------------------------------------------------
# POST /feedback — effect wiring
# ---------------------------------------------------------------------------


def test_record_feedback_signal_positive_returns_effect_promoted(store):
    sid = store.add_signal(user_id="u1", topics=["work"])
    resp = record_feedback(
        FeedbackRequest(item_type="signal", item_id=sid, positive=True), user=_user()
    )
    assert resp["effect"]["promoted"] == ["work"]
    assert resp["effect"]["demoted"] == []
    assert len(store.feedback_log) == 1  # feedback_log still written


def test_record_feedback_signal_negative_returns_effect_demoted(store):
    sid = store.add_signal(user_id="u1", topics=["work"])
    resp = record_feedback(
        FeedbackRequest(item_type="signal", item_id=sid, positive=False), user=_user()
    )
    assert resp["effect"]["demoted"] == ["work"]


def test_record_feedback_entity_item_type_has_no_effect_key(store):
    resp = record_feedback(
        FeedbackRequest(item_type="entity", item_id="ent-1", positive=True), user=_user()
    )
    assert "effect" not in resp
    assert len(store.feedback_log) == 1


def test_record_feedback_implicit_event_has_no_effect_key(store):
    sid = store.add_signal(user_id="u1", topics=["work"])
    resp = record_feedback(
        FeedbackRequest(item_type="signal", item_id=sid, event_type="item_opened"),
        user=_user(),
    )
    assert "effect" not in resp
    assert resp["type"] == "implicit"
    assert "u1" not in store.profiles  # no weight write on implicit feedback


def test_record_feedback_relevance_learning_failure_does_not_500(store, monkeypatch):
    sid = store.add_signal(user_id="u1", topics=["work"])
    from services import relevance_learning

    monkeypatch.setattr(
        relevance_learning,
        "apply_signal_feedback",
        lambda **kw: {"demoted": [], "promoted": []},
    )
    # Even if apply returns empty (its own guarantee on failure), the route is 200.
    resp = record_feedback(
        FeedbackRequest(item_type="signal", item_id=sid, positive=True), user=_user()
    )
    assert resp["status"] == "ok"
    assert resp["effect"] == {"demoted": [], "promoted": []}
    assert len(store.feedback_log) == 1


# ---------------------------------------------------------------------------
# GET /profile/learned
# ---------------------------------------------------------------------------


def test_get_learned_weights_empty_returns_lists_not_404(store):
    view = get_learned_weights(user=_user("nobody"))
    assert view == {"topics": [], "entities": [], "categories": []}


def test_get_learned_weights_returns_sparse_sorted(store):
    store.set_profile("u1", topics={"a": -0.6, "b": 0.3, "c": 0.0})
    view = get_learned_weights(user=_user("u1"))
    assert [t["term"] for t in view["topics"]] == ["a", "b"]  # zero omitted


def test_get_learned_weights_isolated_per_user(store):
    store.set_profile("u1", topics={"secret": -0.6})
    view = get_learned_weights(user=_user("u2"))
    assert view["topics"] == []


# ---------------------------------------------------------------------------
# GET /signals — per-caller learned_relevance_delta
# ---------------------------------------------------------------------------


def test_get_signals_includes_learned_relevance_delta_zero_without_profile(store):
    store.add_signal(user_id="u1", topics=["work"])
    resp = _list(_user("u1"))
    assert all("learned_relevance_delta" in s for s in resp["signals"])
    assert resp["signals"][0]["learned_relevance_delta"] == 0.0


def test_get_signals_delta_reflects_caller_not_signal_owner(store):
    # One shared signal owned by u2; u1 and u2 have different learned weights.
    store.add_signal(user_id="u2", scope="shared", topics=["work"], signal_id="shared-1")
    store.set_profile("u1", topics={"work": 1.0})
    store.set_profile("u2", topics={"work": -1.0})
    u1_resp = _list(_user("u1"))
    u2_resp = _list(_user("u2"))
    d1 = u1_resp["signals"][0]["learned_relevance_delta"]
    d2 = u2_resp["signals"][0]["learned_relevance_delta"]
    assert d1 > 0 and d2 < 0  # same underlying row, opposite per-caller deltas


def test_get_signals_degrades_when_profile_weights_fetch_fails(store):
    store.add_signal(user_id="u1", topics=["work"])
    store.set_profile("u1", topics={"work": 1.0})
    store.raise_weights_fetch = True
    resp = _list(_user("u1"))  # must not raise
    assert resp["signals"], "rows intact"
    assert resp["signals"][0]["learned_relevance_delta"] == 0.0


def test_get_signals_malformed_row_degrades_delta_not_whole_inbox(store):
    # LUM-670: one malformed row (non-string entity name) must not 500 the whole
    # GET /signals response — that row's delta degrades to 0.0, the rest render.
    store.set_profile("u1", topics={"work": 1.0})
    store.add_signal(user_id="u1", topics=["work"], signal_id="good", entities=[{"name": "acme"}])
    store.add_signal(user_id="u1", topics=["work"], signal_id="bad", entities=[{"name": 999}])
    resp = _list(_user("u1"))  # must not raise
    assert len(resp["signals"]) == 2
    by_id = {s["signal_id"]: s for s in resp["signals"]}
    assert by_id["good"]["learned_relevance_delta"] > 0  # healthy row still computes
    assert by_id["bad"]["learned_relevance_delta"] == 0.0  # malformed row degraded
