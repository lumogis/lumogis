# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``/api/v1/kg/*`` — entity card, related, search, GRAPH_MODE guard."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class _KgStore:
    """Tiny MetadataStore for the kg router."""

    def __init__(self):
        self.entities: list[dict] = []
        self.edges: list[dict] = []

    def ping(self) -> bool:
        return True

    def execute(self, query, params=None):
        pass

    def fetch_one(self, query, params=None):
        q = " ".join(query.split()).lower()
        p = params or ()
        if "select entity_id, name, entity_type, aliases, context_tags" in q:
            target = p[-1]
            for e in self.entities:
                if str(e["entity_id"]) == target:
                    return dict(e)
            return None
        if q.startswith("select entity_id from entities") and "limit 1" in q:
            target = p[-1]
            for e in self.entities:
                if str(e["entity_id"]) == target:
                    return {"entity_id": e["entity_id"]}
            return None
        return None

    def fetch_all(self, query, params=None):
        q = " ".join(query.split()).lower()
        p = params or ()
        if "from edge_scores es" in q:
            head = p[0]
            others = []
            for edge in self.edges:
                a, b, w = edge["a"], edge["b"], edge.get("w")
                if a == head or b == head:
                    other_id = b if a == head else a
                    ent = next((e for e in self.entities if str(e["entity_id"]) == other_id), None)
                    if ent is not None:
                        others.append(
                            {
                                "entity_id": ent["entity_id"],
                                "name": ent["name"],
                                "relation": "CO_OCCURS",
                                "weight": w,
                            }
                        )
            others.sort(key=lambda r: (r["weight"] is None, -(r["weight"] or 0)))
            return others[: p[-1]]
        # LUM-581 — shared-projection source ids for the caller.
        if "distinct published_from" in q:
            me = p[0]
            return [
                {"published_from": e["published_from"]}
                for e in self.entities
                if e.get("user_id") == me
                and e.get("scope") == "shared"
                and e.get("published_from") is not None
            ]
        if "from entities" in q and "ilike" in q:
            # Query params: (me, pattern, collapse_me, limit) for the default
            # household-union visibility clause (visible_filter → (me,)).
            me = p[0]
            pattern = p[1].strip("%").lower()
            limit = p[-1]
            out = []
            for e in self.entities:
                if pattern not in e["name"].lower():
                    continue
                scope = e.get("scope", "personal")
                uid = e.get("user_id")
                # Household-union visibility: personal&mine OR shared/system.
                if not ((scope == "personal" and uid == me) or scope in ("shared", "system")):
                    continue
                # LUM-581 owner-projection collapse: hide the caller's own
                # shared projection rows (they see the personal source instead).
                if e.get("published_from") is not None and uid == me:
                    continue
                out.append(
                    {
                        "entity_id": e["entity_id"],
                        "name": e["name"],
                        "entity_type": e.get("entity_type"),
                        "aliases": e.get("aliases", []),
                        "mention_count": e.get("mention_count", 0),
                        "scope": scope,
                        "user_id": uid,
                        "published_from": e.get("published_from"),
                    }
                )
            return out[:limit]
        return []

    def close(self):
        pass


@pytest.fixture
def kg_store(monkeypatch):
    import config as _config

    s = _KgStore()
    _config._instances["metadata_store"] = s
    yield s
    _config._instances.pop("metadata_store", None)


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


def _seed_alice(store):
    store.entities.append(
        {
            "entity_id": "11111111-1111-4111-9111-111111111111",
            "name": "Alice",
            "entity_type": "person",
            "aliases": ["Al"],
            "mention_count": 3,
            "scope": "personal",
            "user_id": "default",
        }
    )


def test_get_entity_returns_card(client, kg_store):
    _seed_alice(kg_store)
    eid = "11111111-1111-4111-9111-111111111111"
    resp = client.get(f"/api/v1/kg/entities/{eid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["entity_id"] == eid
    assert body["name"] == "Alice"
    assert body["aliases"] == ["Al"]
    assert body["scope"] == "personal"


def test_get_entity_unknown_returns_404(client, kg_store):
    resp = client.get("/api/v1/kg/entities/00000000-0000-4000-8000-000000000000")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "entity_not_found"


def test_related_unknown_entity_returns_404(client, kg_store):
    resp = client.get("/api/v1/kg/entities/00000000-0000-4000-8000-000000000000/related")
    assert resp.status_code == 404


def test_related_returns_co_occurs(client, kg_store):
    _seed_alice(kg_store)
    bob_id = "22222222-2222-4222-9222-222222222222"
    kg_store.entities.append(
        {
            "entity_id": bob_id,
            "name": "Bob",
            "entity_type": "person",
            "aliases": [],
            "mention_count": 1,
            "scope": "personal",
            "user_id": "default",
        }
    )
    kg_store.edges.append(
        {
            "a": "11111111-1111-4111-9111-111111111111",
            "b": bob_id,
            "w": 0.42,
        }
    )
    resp = client.get("/api/v1/kg/entities/11111111-1111-4111-9111-111111111111/related")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["related"]) == 1
    assert body["related"][0]["entity_id"] == bob_id
    assert body["related"][0]["relation"] == "CO_OCCURS"
    assert body["related"][0]["weight"] == pytest.approx(0.42)


def test_search_finds_substring(client, kg_store):
    _seed_alice(kg_store)
    resp = client.get("/api/v1/kg/search", params={"q": "ali"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["entities"]) == 1
    assert body["entities"][0]["name"] == "Alice"


def test_search_rejects_blank_q(client, kg_store):
    resp = client.get("/api/v1/kg/search", params={"q": ""})
    assert resp.status_code == 422


def test_graph_mode_service_returns_502(client, kg_store, monkeypatch):
    import config as _cfg

    monkeypatch.setenv("GRAPH_MODE", "service")
    _cfg.set_effective_graph_mode_for_process("service")
    try:
        resp = client.get("/api/v1/kg/search", params={"q": "x"})
    finally:
        _cfg.set_effective_graph_mode_for_process(None)
        _cfg.clear_graph_mode_env_cache()
    assert resp.status_code == 502
    assert resp.json()["detail"]["error"] == "kg_unavailable"


# ---------------------------------------------------------------------------
# LUM-581 — household entity sharing: is_shared / is_owner derivation + collapse
# ---------------------------------------------------------------------------

ALICE_SRC = "11111111-1111-4111-9111-111111111111"
ALICE_PROJ = "aaaaaaaa-aaaa-4aaa-9aaa-aaaaaaaaaaaa"
BOB_PROJ = "bbbbbbbb-bbbb-4bbb-9bbb-bbbbbbbbbbbb"


def _seed_shared_projection(store, *, entity_id, published_from, user_id, name):
    store.entities.append(
        {
            "entity_id": entity_id,
            "name": name,
            "entity_type": "person",
            "aliases": [],
            "mention_count": 1,
            "scope": "shared",
            "user_id": user_id,
            "published_from": published_from,
        }
    )


def test_get_entity_owner_personal_unshared_defaults(client, kg_store):
    _seed_alice(kg_store)
    resp = client.get(f"/api/v1/kg/entities/{ALICE_SRC}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["share_status"] == "personal"
    assert body["is_owner"] is True


def test_get_entity_owner_shows_shared_when_projection_exists(client, kg_store):
    _seed_alice(kg_store)
    _seed_shared_projection(
        kg_store,
        entity_id=ALICE_PROJ,
        published_from=ALICE_SRC,
        user_id="default",
        name="Alice",
    )
    resp = client.get(f"/api/v1/kg/entities/{ALICE_SRC}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["share_status"] == "shared"
    assert body["is_owner"] is True


def test_search_collapses_owner_projection(client, kg_store):
    """Owner sees ONE row (the personal source, marked shared) — not a duplicate."""
    _seed_alice(kg_store)
    _seed_shared_projection(
        kg_store,
        entity_id=ALICE_PROJ,
        published_from=ALICE_SRC,
        user_id="default",
        name="Alice",
    )
    resp = client.get("/api/v1/kg/search", params={"q": "ali"})
    assert resp.status_code == 200
    entities = resp.json()["entities"]
    assert len(entities) == 1
    assert entities[0]["entity_id"] == ALICE_SRC
    assert entities[0]["share_status"] == "shared"
    assert entities[0]["is_owner"] is True


def test_search_member_sees_shared_projection_as_non_owner(client, kg_store):
    """A projection owned by another member is visible with is_owner=false."""
    _seed_shared_projection(
        kg_store,
        entity_id=BOB_PROJ,
        published_from="99999999-9999-4999-9999-999999999999",
        user_id="bob",
        name="Bespoke",
    )
    resp = client.get("/api/v1/kg/search", params={"q": "bespoke"})
    assert resp.status_code == 200
    entities = resp.json()["entities"]
    assert len(entities) == 1
    assert entities[0]["entity_id"] == BOB_PROJ
    assert entities[0]["share_status"] == "shared"
    assert entities[0]["is_owner"] is False


def test_search_excludes_other_users_personal_entity(client, kg_store):
    """A member's UNSHARED personal entity never appears in another member's list."""
    kg_store.entities.append(
        {
            "entity_id": "cccccccc-cccc-4ccc-9ccc-cccccccccccc",
            "name": "Carol Private",
            "entity_type": "person",
            "aliases": [],
            "mention_count": 1,
            "scope": "personal",
            "user_id": "carol",
            "published_from": None,
        }
    )
    resp = client.get("/api/v1/kg/search", params={"q": "carol"})
    assert resp.status_code == 200
    assert resp.json()["entities"] == []


# ---------------------------------------------------------------------------
# Pure derivation helper (no DB) — the core LUM-581 logic.
# ---------------------------------------------------------------------------


def test_derive_share_fields_owner_personal_no_projection():
    from routes.api_v1.kg import _derive_entity_share_fields

    row = {"entity_id": ALICE_SRC, "user_id": "default", "scope": "personal"}
    assert _derive_entity_share_fields(
        row, caller_user_id="default", shared_source_ids=set()
    ) == ("personal", True)


def test_derive_share_fields_owner_personal_with_projection():
    from routes.api_v1.kg import _derive_entity_share_fields

    row = {"entity_id": ALICE_SRC, "user_id": "default", "scope": "personal"}
    assert _derive_entity_share_fields(
        row, caller_user_id="default", shared_source_ids={ALICE_SRC}
    ) == ("shared", True)


def test_derive_share_fields_member_views_foreign_projection():
    from routes.api_v1.kg import _derive_entity_share_fields

    row = {"entity_id": BOB_PROJ, "user_id": "bob", "scope": "shared"}
    assert _derive_entity_share_fields(
        row, caller_user_id="default", shared_source_ids=set()
    ) == ("shared", False)


def test_derive_share_fields_owner_views_own_projection_directly():
    from routes.api_v1.kg import _derive_entity_share_fields

    row = {"entity_id": ALICE_PROJ, "user_id": "default", "scope": "shared"}
    assert _derive_entity_share_fields(
        row, caller_user_id="default", shared_source_ids=set()
    ) == ("shared", True)
