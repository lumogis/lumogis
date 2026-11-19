# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live two-member proof that ``/api/v1/kg/*`` serves in every graph mode (LUM-964).

Real Postgres, real SQL (``visible_filter`` + the ``/related`` ``e_where``
rewrite). Proves that:

* the three routes answer the same in ``disabled``, ``inprocess`` and ``service``;
* a member never sees another member's personal entity;
* ``/related`` returns only the caller's own ``edge_scores`` rows.

Visibility here comes from the ``users`` table, not from the dependency
override: the handlers rebuild ``UserContext(user_id=...)`` and
``visible_filter`` resolves ``allows_shared`` through the users service, which
returns ``True`` for a missing row. So the seed must NOT insert ``users`` rows
for the ``lum964-*`` members.

Needs Postgres only. Gate: ``make test-env-integration``.
"""

# ruff: noqa: F811  (fixtures imported from _kg_live are used as test parameters)

from __future__ import annotations

import uuid

import pytest
from tests.integration._kg_live import MODES
from tests.integration._kg_live import _as
from tests.integration._kg_live import _insert_entity
from tests.integration._kg_live import graph_mode  # noqa: F401 — fixture
from tests.integration._kg_live import kg_app  # noqa: F401 — fixture
from tests.integration._kg_live import live_pg  # noqa: F401 — fixture

import config


def _insert_score(ms, owner, x, y, quality) -> None:
    a, b = sorted((x, y), key=uuid.UUID)
    ms.execute(
        "INSERT INTO edge_scores (user_id, entity_id_a, entity_id_b, ppmi_score, edge_quality) "
        "VALUES (%s, %s, %s, %s, %s)",
        (owner, a, b, quality, quality),
    )


@pytest.fixture
def seeded(live_pg):
    ms = live_pg
    suffix = uuid.uuid4().hex[:8]
    alice = f"lum964-alice-{suffix}"
    bob = f"lum964-bob-{suffix}"
    try:
        b1 = _insert_entity(ms, name=f"Lum964 Bob One {suffix}", owner=bob)
        b2 = _insert_entity(ms, name=f"Lum964 Bob Two {suffix}", owner=bob)
        a1 = _insert_entity(ms, name=f"Lum964 Alice One {suffix}", owner=alice)
        a2 = _insert_entity(ms, name=f"Lum964 Alice Two {suffix}", owner=alice)
        s1 = _insert_entity(
            ms,
            name=f"Lum964 Shared One {suffix}",
            owner=alice,
            scope="shared",
            published_from=a1,
        )
        s2 = _insert_entity(
            ms,
            name=f"Lum964 Shared Two {suffix}",
            owner=alice,
            scope="shared",
            published_from=a2,
        )
        _insert_score(ms, bob, b1, b2, 0.5)
        _insert_score(ms, alice, a1, a2, 0.9)
        # Cross-member row no shipped writer produces, seeded on purpose: both
        # ends are visible to Bob, the score belongs to Alice.
        _insert_score(ms, alice, s1, s2, 0.7)
        yield {
            "suffix": suffix,
            "alice": alice,
            "bob": bob,
            "B1": b1,
            "B2": b2,
            "A1": a1,
            "A2": a2,
            "S1": s1,
            "S2": s2,
        }
    finally:
        ms.execute("DELETE FROM entities WHERE user_id IN (%s, %s)", (alice, bob))


def test_related_returns_only_callers_scores(seeded, kg_app, monkeypatch, graph_mode):
    s = seeded
    resp = _as(kg_app, monkeypatch, s["bob"]).get(f"/api/v1/kg/entities/{s['B1']}/related")
    assert resp.status_code == 200
    related = resp.json()["related"]
    assert len(related) == 1
    assert related[0]["entity_id"] == s["B2"]
    assert related[0]["name"] == f"Lum964 Bob Two {s['suffix']}"
    assert related[0]["relation"] == "CO_OCCURS"
    assert related[0]["weight"] == pytest.approx(0.5)


def test_related_excludes_other_members_scores(seeded, kg_app, monkeypatch, graph_mode):
    s = seeded
    bob = _as(kg_app, monkeypatch, s["bob"])
    resp = bob.get(f"/api/v1/kg/entities/{s['S1']}/related")
    assert resp.status_code == 200
    assert resp.json()["related"] == []

    alice = _as(kg_app, monkeypatch, s["alice"])
    resp = alice.get(f"/api/v1/kg/entities/{s['S1']}/related")
    assert resp.status_code == 200
    rel = resp.json()["related"]
    assert [r["entity_id"] for r in rel] == [s["S2"]]
    assert rel[0]["weight"] == pytest.approx(0.7)

    resp = alice.get(f"/api/v1/kg/entities/{s['A1']}/related")
    assert resp.status_code == 200
    rel = resp.json()["related"]
    assert [r["entity_id"] for r in rel] == [s["A2"]]
    assert rel[0]["weight"] == pytest.approx(0.9)


def test_other_members_personal_entity_hidden_in_every_mode(
    seeded, kg_app, monkeypatch, graph_mode
):
    s = seeded
    bob = _as(kg_app, monkeypatch, s["bob"])

    card = bob.get(f"/api/v1/kg/entities/{s['A1']}")
    assert card.status_code == 404
    assert card.json()["detail"]["error"] == "entity_not_found"
    related = bob.get(f"/api/v1/kg/entities/{s['A1']}/related")
    assert related.status_code == 404
    search = bob.get("/api/v1/kg/search", params={"q": s["suffix"], "limit": 50})
    assert search.status_code == 200
    ids = {e["entity_id"] for e in search.json()["entities"]}
    assert ids == {s["B1"], s["B2"], s["S1"], s["S2"]}
    for resp in (card, related, search):
        assert "kg_unavailable" not in resp.text


def test_card_and_search_identical_across_modes(seeded, kg_app, monkeypatch):
    s = seeded
    bob = _as(kg_app, monkeypatch, s["bob"])
    cards, searches = [], []
    try:
        for mode in MODES:
            config.set_effective_graph_mode_for_process(mode)
            card = bob.get(f"/api/v1/kg/entities/{s['B1']}")
            search = bob.get("/api/v1/kg/search", params={"q": s["suffix"], "limit": 50})
            assert card.status_code == 200
            assert search.status_code == 200
            cards.append(card.json())
            searches.append(sorted(search.json()["entities"], key=lambda e: e["entity_id"]))
    finally:
        config.set_effective_graph_mode_for_process(None)
        config.clear_graph_mode_env_cache()
    assert cards[0] == cards[1] == cards[2]
    assert searches[0] == searches[1] == searches[2]
