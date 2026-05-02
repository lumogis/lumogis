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
        if "from entities" in q and "ilike" in q:
            pattern = p[-2].strip("%").lower()
            return [
                {
                    "entity_id": e["entity_id"],
                    "name": e["name"],
                    "entity_type": e.get("entity_type"),
                    "aliases": e.get("aliases", []),
                    "mention_count": e.get("mention_count", 0),
                    "scope": e.get("scope", "personal"),
                    "user_id": e.get("user_id"),
                }
                for e in self.entities
                if pattern in e["name"].lower()
            ][: p[-1]]
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
    monkeypatch.setenv("GRAPH_MODE", "service")
    resp = client.get("/api/v1/kg/search", params={"q": "x"})
    assert resp.status_code == 502
    assert resp.json()["detail"]["error"] == "kg_unavailable"
