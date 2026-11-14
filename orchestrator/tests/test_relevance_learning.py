# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-649 — feedback → relevance learning: unit tests.

Covers the pure helpers (``_avg_matching_weight``, ``_signal_learning_terms``,
``compute_learned_relevance_delta``, ``compute_ingest_learned_component``,
``fetch_learned_weights_view``) directly, and the DB-touching write/read paths
(``apply_signal_feedback``, ``load_profile_weights``) through a fake metadata
store swapped in at the ``config.get_metadata_store`` boundary. A real-Postgres
companion (``test_relevance_learning_live.py``) executes the UPSERT against the
058 schema.
"""

from __future__ import annotations

import json

import pytest
from auth import UserContext
from models.signals import RelevanceProfile
from models.signals import Signal

import config
from services import relevance_learning as rl

# ---------------------------------------------------------------------------
# Fake store
# ---------------------------------------------------------------------------


class FakeStore:
    """In-memory stand-in honouring the exact queries relevance_learning issues.

    Holds one ``signals`` row keyed by signal_id and one ``relevance_profiles``
    row keyed by user_id. Understands the visibility-scoped signal SELECT, the
    weights SELECT, and the learned-weights UPSERT.
    """

    def __init__(self):
        self.signals: dict[str, dict] = {}
        self.profiles: dict[str, dict] = {}
        self.raise_on_execute: Exception | None = None
        self.raise_on_fetch: Exception | None = None

    def add_signal(
        self,
        *,
        signal_id,
        user_id,
        scope="personal",
        topics=None,
        entities=None,
        from_addr="",
        category="",
    ):
        self.signals[signal_id] = {
            "signal_id": signal_id,
            "user_id": user_id,
            "scope": scope,
            "topics": topics or [],
            "entities": entities or [],
            "from_addr": from_addr,
            "category": category,
        }

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        if self.raise_on_fetch is not None:
            raise self.raise_on_fetch
        params = params or ()
        if "FROM signals" in query:
            # params = (signal_id, *visible_filter_params). visible_filter for a
            # household-union caller is "((scope='personal' AND user_id=%s) OR
            # scope IN ('shared','system'))"; for allows_shared=False it is
            # "((scope='personal' AND user_id=%s) OR scope='system')". We emulate
            # by reading the caller from params and the clause text from query.
            signal_id = params[0]
            row = self.signals.get(signal_id)
            if row is None:
                return None
            caller = params[1] if len(params) > 1 else None
            shared_ok = "scope IN ('shared','system')" in query
            if row["scope"] == "personal":
                visible = row["user_id"] == caller
            elif row["scope"] == "shared":
                visible = shared_ok
            else:  # system
                visible = True
            if not visible:
                return None
            return {k: row[k] for k in ("topics", "entities", "from_addr", "category")}
        if "FROM relevance_profiles" in query:
            user_id = params[0]
            prof = self.profiles.get(user_id)
            if prof is None:
                return None
            return {
                "learned_topic_weights": prof.get("topics", {}),
                "learned_entity_weights": prof.get("entities", {}),
                "learned_category_weights": prof.get("categories", {}),
            }
        return None

    def execute(self, query: str, params: tuple | None = None) -> None:
        if self.raise_on_execute is not None:
            raise self.raise_on_execute
        params = params or ()
        if "INSERT INTO relevance_profiles" in query:
            user_id = params[0]
            self.profiles[user_id] = {
                "topics": json.loads(params[1]),
                "entities": json.loads(params[2]),
                "categories": json.loads(params[3]),
            }


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)
    return s


def _user(user_id="u1", allows_shared=True):
    # role="user" + allows_shared explicit → visibility honours allows_shared.
    return UserContext(
        user_id=user_id, is_authenticated=True, role="user", allows_shared=allows_shared
    )


# ---------------------------------------------------------------------------
# _avg_matching_weight — non-dilution semantics
# ---------------------------------------------------------------------------


def test_avg_matching_weight_ignores_unlearned_cooccurring_terms():
    # Only "work" is learned; "hobby"/"weather" are absent from the map and must
    # be excluded from the average, not treated as implicit zeros (0.6, not 0.2).
    assert rl._avg_matching_weight({"work": 0.6}, ["work", "hobby", "weather"]) == 0.6


def test_avg_matching_weight_empty_map_is_zero():
    assert rl._avg_matching_weight({}, ["anything"]) == 0.0


def test_avg_matching_weight_empty_terms_is_zero():
    assert rl._avg_matching_weight({"x": 0.5}, []) == 0.0


def test_avg_matching_weight_averages_multiple_matches():
    assert rl._avg_matching_weight({"a": 0.4, "b": 0.6}, ["a", "b"]) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# _signal_learning_terms — normalization + sender folding
# ---------------------------------------------------------------------------


def test_signal_learning_terms_normalizes_case():
    terms = rl._signal_learning_terms(
        {
            "topics": ["Work"],
            "entities": [{"name": "Acme Corp"}],
            "from_addr": "",
            "category": "Newsletter",
        }
    )
    assert terms == {"topics": ["work"], "entities": ["acme corp"], "categories": ["newsletter"]}


def test_signal_learning_terms_folds_sender_into_entities():
    terms = rl._signal_learning_terms(
        {
            "topics": [],
            "entities": [{"name": "Acme"}],
            "from_addr": "Promo@Retailer.com",
            "category": "",
        }
    )
    assert terms["entities"] == ["acme", "promo@retailer.com"]
    assert terms["categories"] == []


# ---------------------------------------------------------------------------
# apply_signal_feedback — write path
# ---------------------------------------------------------------------------


def test_apply_feedback_positive_bumps_topic_and_entity_weights(store):
    store.add_signal(signal_id="s1", user_id="u1", topics=["work"], entities=[{"name": "acme"}])
    effect = rl.apply_signal_feedback(user=_user(), signal_id="s1", positive=True)
    assert set(effect["promoted"]) == {"work", "acme"}
    assert effect["demoted"] == []
    assert store.profiles["u1"]["topics"]["work"] == pytest.approx(0.15)
    assert store.profiles["u1"]["entities"]["acme"] == pytest.approx(0.15)


def test_apply_feedback_negative_demotes_weights(store):
    store.add_signal(signal_id="s1", user_id="u1", topics=["work"])
    effect = rl.apply_signal_feedback(user=_user(), signal_id="s1", positive=False)
    assert effect["demoted"] == ["work"]
    assert effect["promoted"] == []
    assert store.profiles["u1"]["topics"]["work"] == pytest.approx(-0.15)


def test_apply_feedback_clamps_at_lower_bound(store):
    store.add_signal(signal_id="s1", user_id="u1", topics=["work"])
    for _ in range(10):  # 10 × -0.15 = -1.5, clamped to -1.0
        rl.apply_signal_feedback(user=_user(), signal_id="s1", positive=False)
    assert store.profiles["u1"]["topics"]["work"] == pytest.approx(rl.WEIGHT_MIN)


def test_apply_feedback_creates_profile_row_when_none_exists(store):
    store.add_signal(signal_id="s1", user_id="u1", topics=["work"])
    assert "u1" not in store.profiles
    rl.apply_signal_feedback(user=_user(), signal_id="s1", positive=True)
    assert "u1" in store.profiles


def test_apply_feedback_folds_sender_into_entity_weights(store):
    store.add_signal(
        signal_id="s1", user_id="u1", entities=[{"name": "acme"}], from_addr="promo@retailer.com"
    )
    effect = rl.apply_signal_feedback(user=_user(), signal_id="s1", positive=False)
    assert "promo@retailer.com" in effect["demoted"]
    assert store.profiles["u1"]["entities"]["promo@retailer.com"] == pytest.approx(-0.15)


def test_apply_feedback_uses_category_as_the_learned_axis(store):
    store.add_signal(signal_id="s1", user_id="u1", category="newsletter")
    rl.apply_signal_feedback(user=_user(), signal_id="s1", positive=False)
    assert store.profiles["u1"]["categories"] == {"newsletter": pytest.approx(-0.15)}


def test_apply_feedback_missing_signal_returns_empty_effect_no_exception(store):
    effect = rl.apply_signal_feedback(user=_user(), signal_id="nope", positive=True)
    assert effect == {"demoted": [], "promoted": []}
    assert "u1" not in store.profiles


def test_apply_feedback_effect_is_one_directional(store):
    store.add_signal(signal_id="s1", user_id="u1", topics=["work"], entities=[{"name": "acme"}])
    effect = rl.apply_signal_feedback(user=_user(), signal_id="s1", positive=True)
    assert not (effect["demoted"] and effect["promoted"])


def test_apply_feedback_dedupes_touched_terms(store):
    # "acme" appears as both a topic and an entity name → named once.
    store.add_signal(signal_id="s1", user_id="u1", topics=["acme"], entities=[{"name": "acme"}])
    effect = rl.apply_signal_feedback(user=_user(), signal_id="s1", positive=True)
    assert effect["promoted"].count("acme") == 1


def test_apply_feedback_invisible_signal_returns_empty_effect_no_disclosure(store):
    # A signal owned by u2, scope=personal — u1 cannot see it.
    store.add_signal(signal_id="s1", user_id="u2", scope="personal", topics=["secret"])
    effect = rl.apply_signal_feedback(user=_user("u1"), signal_id="s1", positive=False)
    assert effect == {"demoted": [], "promoted": []}
    assert "u1" not in store.profiles


def test_apply_feedback_invisible_signal_when_caller_opted_out_of_shared(store):
    # A shared signal owned by u2; u1 has allows_shared=False → cannot see it.
    store.add_signal(signal_id="s1", user_id="u2", scope="shared", topics=["shared-topic"])
    effect = rl.apply_signal_feedback(
        user=_user("u1", allows_shared=False), signal_id="s1", positive=False
    )
    assert effect == {"demoted": [], "promoted": []}
    assert "u1" not in store.profiles


def test_apply_feedback_shared_signal_visible_when_opted_in(store):
    # Same shared signal, but u1 allows_shared=True → visible, feedback applies.
    store.add_signal(signal_id="s1", user_id="u2", scope="shared", topics=["shared-topic"])
    effect = rl.apply_signal_feedback(
        user=_user("u1", allows_shared=True), signal_id="s1", positive=False
    )
    assert effect["demoted"] == ["shared-topic"]


def test_apply_feedback_db_write_failure_returns_empty_effect_no_raise(store):
    store.add_signal(signal_id="s1", user_id="u1", topics=["work"])
    store.raise_on_execute = RuntimeError("db down")
    effect = rl.apply_signal_feedback(user=_user(), signal_id="s1", positive=True)
    assert effect == {"demoted": [], "promoted": []}


# ---------------------------------------------------------------------------
# compute_learned_relevance_delta — read path 2
# ---------------------------------------------------------------------------


def test_compute_delta_zero_when_no_profile():
    assert (
        rl.compute_learned_relevance_delta(
            None, topics=["work"], entities=[], from_addr="", category=""
        )
        == 0.0
    )


def test_compute_delta_positive_for_promoted_topic():
    weights = {"topics": {"work": 1.0}, "entities": {}, "categories": {}}
    delta = rl.compute_learned_relevance_delta(
        weights, topics=["work"], entities=[], from_addr="", category=""
    )
    assert delta == pytest.approx(0.30)


def test_compute_delta_clamped_to_bounds():
    weights = {
        "topics": {"a": 1.0},
        "entities": {"e": 1.0},
        "categories": {"c": 1.0},
    }
    delta = rl.compute_learned_relevance_delta(
        weights, topics=["a"], entities=[{"name": "e"}], from_addr="", category="c"
    )
    # 0.30 + 0.20 + 0.20 = 0.70 theoretical, clamped to 0.6.
    assert delta == pytest.approx(0.6)


def test_compute_delta_case_insensitive_matching():
    weights = {"entities": {"acme corp": 1.0}, "topics": {}, "categories": {}}
    delta = rl.compute_learned_relevance_delta(
        weights, topics=[], entities=[{"name": "ACME Corp"}], from_addr="", category=""
    )
    assert delta == pytest.approx(0.20)


def test_compute_delta_zero_when_all_weight_maps_empty(monkeypatch):
    # LUM-671: a caller with a profile row but no learned weights (all-empty maps)
    # short-circuits to 0.0 WITHOUT extracting terms from the row.
    called = False

    def _boom(*a, **k):
        nonlocal called
        called = True
        raise AssertionError("_signal_learning_terms should not run for empty maps")

    monkeypatch.setattr(rl, "_signal_learning_terms", _boom)
    empty = {"topics": {}, "entities": {}, "categories": {}}
    assert (
        rl.compute_learned_relevance_delta(
            empty, topics=["work"], entities=[{"name": "acme"}], from_addr="", category="news"
        )
        == 0.0
    )
    assert called is False


def test_compute_delta_malformed_row_degrades_to_zero_not_raise():
    # LUM-670: a malformed entity (non-string name) on a signal a caller HAS learned
    # weights for must degrade this row's delta to 0.0, never raise (which would 500
    # the whole GET /signals response).
    weights = {"topics": {"work": 0.5}, "entities": {}, "categories": {}}
    delta = rl.compute_learned_relevance_delta(
        weights, topics=["work"], entities=[{"name": 12345}], from_addr="", category=""
    )
    assert delta == 0.0


# ---------------------------------------------------------------------------
# compute_ingest_learned_component — read path 1
# ---------------------------------------------------------------------------


def _signal(**kw):
    import uuid
    from datetime import datetime
    from datetime import timezone

    defaults = dict(
        signal_id=str(uuid.uuid4()),
        source_id="s",
        title="t",
        url="u",
        published_at=datetime.now(timezone.utc),
        content_summary="c",
        raw_content="",
        entities=[],
        topics=[],
        importance_score=0.5,
        relevance_score=0.0,
        notified=False,
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(kw)
    return Signal(**defaults)


def test_ingest_component_zero_for_empty_profile():
    profile = RelevanceProfile(id="p")
    signal = _signal(topics=["work"], category="newsletter")
    assert rl.compute_ingest_learned_component(profile, signal) == 0.0


def test_ingest_component_malformed_signal_degrades_to_zero_not_raise():
    # LUM-670: a malformed entity at ingest must never crash the poll — the learned
    # component degrades to 0.0 (relevance_score falls back to the 5-term sum).
    profile = RelevanceProfile(id="p", learned_entity_weights={"acme": 0.5})
    signal = _signal(entities=[{"name": 999}])
    assert rl.compute_ingest_learned_component(profile, signal) == 0.0


def test_ingest_component_positive_for_promoted_topic():
    profile = RelevanceProfile(id="p", learned_topic_weights={"work": 1.0})
    signal = _signal(topics=["work"])
    # 0.5 × 1.0 = 0.5, clamped to 0.3.
    assert rl.compute_ingest_learned_component(profile, signal) == pytest.approx(0.3)


def test_ingest_component_negative_floors_at_minus_point_three():
    profile = RelevanceProfile(
        id="p",
        learned_topic_weights={"work": -1.0},
        learned_entity_weights={"acme": -1.0},
        learned_category_weights={"news": -1.0},
    )
    signal = _signal(topics=["work"], entities=[{"name": "acme"}], category="news")
    assert rl.compute_ingest_learned_component(profile, signal) == pytest.approx(-0.3)


# ---------------------------------------------------------------------------
# fetch_learned_weights_view — GET /profile/learned shape
# ---------------------------------------------------------------------------


def test_fetch_view_omits_zero_and_sorts_demoted_asc_promoted_desc(store):
    store.profiles["u1"] = {
        "topics": {"a": -0.3, "b": -0.6, "c": 0.0, "d": 0.15, "e": 0.45},
        "entities": {},
        "categories": {},
    }
    view = rl.fetch_learned_weights_view("u1")
    topics = view["topics"]
    # Zero omitted; demoted ascending (most-negative first) then promoted descending.
    assert [t["term"] for t in topics] == ["b", "a", "e", "d"]
    assert view["entities"] == []
    assert view["categories"] == []


def test_fetch_view_empty_profile_returns_empty_lists(store):
    view = rl.fetch_learned_weights_view("nobody")
    assert view == {"topics": [], "entities": [], "categories": []}


# ---------------------------------------------------------------------------
# load_profile_weights — degrade contract
# ---------------------------------------------------------------------------


def test_load_profile_weights_degrades_on_db_error(store):
    store.raise_on_fetch = RuntimeError("db down")
    weights = rl.load_profile_weights("u1")
    assert weights == {"topics": {}, "entities": {}, "categories": {}}
