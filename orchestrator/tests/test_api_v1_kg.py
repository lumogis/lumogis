# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``/api/v1/kg/*`` — entity card, related, search in every GRAPH_MODE."""

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
            # Query params: (me, hider, pattern, collapse_me, limit): the household-union
            # visibility clause (visible_filter → (me,)), the Hide clause
            # (not_hidden_sql → (me,), LUM-966), then the rest.
            me = p[0]
            pattern = p[2].strip("%").lower()
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


@pytest.fixture(params=["disabled", "inprocess", "service"])
def graph_mode(request):
    """Set the *effective* graph mode (enter after ``client``: lifespan resets it)."""
    import config as _cfg

    _cfg.set_effective_graph_mode_for_process(request.param)
    yield request.param
    _cfg.set_effective_graph_mode_for_process(None)
    _cfg.clear_graph_mode_env_cache()


def test_kg_routes_serve_in_every_graph_mode(client, kg_store, graph_mode):
    """LUM-964: card, related and search answer identically in every graph mode."""
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
    kg_store.edges.append({"a": "11111111-1111-4111-9111-111111111111", "b": bob_id, "w": 0.42})
    alice = "11111111-1111-4111-9111-111111111111"

    card = client.get(f"/api/v1/kg/entities/{alice}")
    related = client.get(f"/api/v1/kg/entities/{alice}/related")
    search = client.get("/api/v1/kg/search", params={"q": "ali"})

    for resp in (card, related, search):
        assert resp.status_code == 200
        assert "kg_unavailable" not in resp.text
    assert card.json() == {
        "entity_id": alice,
        "name": "Alice",
        "type": "person",
        "aliases": ["Al"],
        "summary": None,
        "sources": [],
        "scope": "personal",
        "owner_user_id": None,
        "share_status": "personal",
        "is_owner": True,
        "version": None,
        "hidden": False,
    }
    rel = related.json()["related"]
    assert len(rel) == 1
    assert rel[0]["entity_id"] == bob_id
    assert rel[0]["name"] == "Bob"
    assert rel[0]["relation"] == "CO_OCCURS"
    assert rel[0]["weight"] == pytest.approx(0.42)
    found = search.json()["entities"]
    assert len(found) == 1
    assert found[0]["entity_id"] == alice
    assert found[0]["name"] == "Alice"


def test_kg_routes_404_in_every_graph_mode(client, kg_store, graph_mode):
    missing = "00000000-0000-4000-8000-000000000000"
    for path in (f"/api/v1/kg/entities/{missing}", f"/api/v1/kg/entities/{missing}/related"):
        resp = client.get(path)
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "entity_not_found"


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
    assert _derive_entity_share_fields(row, caller_user_id="default", shared_source_ids=set()) == (
        "personal",
        True,
    )


def test_derive_share_fields_owner_personal_with_projection():
    from routes.api_v1.kg import _derive_entity_share_fields

    row = {"entity_id": ALICE_SRC, "user_id": "default", "scope": "personal"}
    assert _derive_entity_share_fields(
        row, caller_user_id="default", shared_source_ids={ALICE_SRC}
    ) == ("shared", True)


def test_derive_share_fields_member_views_foreign_projection():
    from routes.api_v1.kg import _derive_entity_share_fields

    row = {"entity_id": BOB_PROJ, "user_id": "bob", "scope": "shared"}
    assert _derive_entity_share_fields(row, caller_user_id="default", shared_source_ids=set()) == (
        "shared",
        False,
    )


def test_derive_share_fields_owner_views_own_projection_directly():
    from routes.api_v1.kg import _derive_entity_share_fields

    row = {"entity_id": ALICE_PROJ, "user_id": "default", "scope": "shared"}
    assert _derive_entity_share_fields(row, caller_user_id="default", shared_source_ids=set()) == (
        "shared",
        True,
    )


# ---------------------------------------------------------------------------
# LUM-161 — "What Lumogis knows": list, mentions, PATCH (status mapping only).
# The service layer is patched: these tests prove exception → status/body
# mapping, never visibility (that is the live suite's job, with real Postgres).
# ---------------------------------------------------------------------------

_EID = "11111111-1111-4111-9111-111111111111"


def _list_response():
    from models.api_v1 import EntityKindCounts
    from models.api_v1 import EntityListItem
    from models.api_v1 import EntityListResponse

    return EntityListResponse(
        entities=[
            EntityListItem(
                entity_id=_EID,
                name="Dr. Meier",
                type="PERSON",
                kind="person",
                aliases=["Meier"],
                alias_count=1,
                mention_count=2,
            )
        ],
        counts=EntityKindCounts(person=1),
        total=1,
    )


def _patch_service(monkeypatch, module, name, result=None, exc=None):
    def _fn(*a, **kw):
        if exc is not None:
            raise exc
        return result

    monkeypatch.setattr(module, name, _fn)


def test_list_entities_200_shape(client, kg_store, monkeypatch):
    from services import entity_browser

    _patch_service(monkeypatch, entity_browser, "list_entities", _list_response())
    resp = client.get("/api/v1/kg/entities")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"entities", "counts", "total"}
    assert set(body["counts"]) == {"person", "org", "project", "concept", "other"}
    assert "alias_count" in body["entities"][0]


def test_list_entities_passes_query_through(client, kg_store, monkeypatch):
    from services import entity_browser

    seen = {}

    def _fn(user, **kw):
        seen.update(kw)
        return _list_response()

    monkeypatch.setattr(entity_browser, "list_entities", _fn)
    resp = client.get(
        "/api/v1/kg/entities",
        params={"kind": "org", "shared": "only", "sort": "name", "limit": 10, "offset": 20},
    )
    assert resp.status_code == 200
    assert seen == {"kind": "org", "shared": "only", "sort": "name", "limit": 10, "offset": 20}


@pytest.mark.parametrize(
    "params",
    [
        {"kind": "place"},
        {"limit": 201},
        {"limit": 0},
        {"offset": -1},
        {"sort": "name; DROP TABLE"},
        {"shared": "maybe"},
    ],
)
def test_list_entities_bad_params_422(client, kg_store, monkeypatch, params):
    from services import entity_browser

    _patch_service(monkeypatch, entity_browser, "list_entities", _list_response())
    assert client.get("/api/v1/kg/entities", params=params).status_code == 422


def test_list_entities_503_on_db_error(client, kg_store, monkeypatch):
    from services import entity_browser

    _patch_service(
        monkeypatch, entity_browser, "list_entities", exc=entity_browser.EntityStoreUnavailable()
    )
    resp = client.get("/api/v1/kg/entities")
    assert resp.status_code == 503
    assert resp.json() == {"detail": {"error": "store_unavailable", "code": "store_unavailable"}}


def test_mentions_404_when_not_visible(client, kg_store, monkeypatch):
    from services import entity_browser

    _patch_service(monkeypatch, entity_browser, "list_mentions", None)
    resp = client.get(f"/api/v1/kg/entities/{_EID}/mentions")
    assert resp.status_code == 404
    assert resp.json()["detail"] == {"error": "entity_not_found", "code": "entity_not_found"}


def test_mentions_503_on_body_db_error(client, kg_store, monkeypatch):
    from services import entity_browser

    _patch_service(
        monkeypatch, entity_browser, "list_mentions", exc=entity_browser.EntityStoreUnavailable()
    )
    assert client.get(f"/api/v1/kg/entities/{_EID}/mentions").status_code == 503


def test_mentions_200_shape(client, kg_store, monkeypatch):
    from models.api_v1 import EntityMentionCounts
    from models.api_v1 import EntityMentionsResponse

    from services import entity_browser

    _patch_service(
        monkeypatch,
        entity_browser,
        "list_mentions",
        EntityMentionsResponse(mentions=[], counts=EntityMentionCounts(document=1), total=1),
    )
    resp = client.get(f"/api/v1/kg/entities/{_EID}/mentions")
    assert resp.status_code == 200
    assert set(resp.json()["counts"]) == {"document", "chat", "ai_tool"}


def _row(**over):
    row = {
        "entity_id": _EID,
        "name": "Hans Meier",
        "entity_type": "PERSON",
        "aliases": ["Dr. Meier"],
        "context_tags": [],
        "scope": "personal",
        "user_id": "default",
        "published_from": None,
        "version": 2,
    }
    row.update(over)
    return row


def _patch(client, **body):
    body.setdefault("version", 1)
    return client.patch(f"/api/v1/kg/entities/{_EID}", json=body)


def test_patch_200_returns_card_with_new_version(client, kg_store, monkeypatch):
    from services import entity_edit

    _patch_service(monkeypatch, entity_edit, "rename_entity", _row())
    resp = _patch(client, name="Hans Meier")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Hans Meier"
    assert body["version"] == 2
    assert body["aliases"] == ["Dr. Meier"]


def test_patch_passes_deltas_to_service(client, kg_store, monkeypatch):
    from services import entity_edit

    seen = {}

    def _fn(user, entity_id, **kw):
        seen.update(kw)
        seen["entity_id"] = entity_id
        return _row()

    monkeypatch.setattr(entity_edit, "rename_entity", _fn)
    _patch(client, name="X", aliases_add=["a"], aliases_remove=["b"], version=7)
    assert seen == {
        "entity_id": _EID,
        "name": "X",
        "aliases_add": ["a"],
        "aliases_remove": ["b"],
        "version": 7,
    }


@pytest.mark.parametrize(
    "exc_factory, status_code, code, extra",
    [
        (lambda m: m.EntityNotFound("x"), 404, "entity_not_found", {}),
        (lambda m: m.EntityNotOwner("x"), 403, "not_owner", {}),
        (
            lambda m: m.EntityNotEditable("src-1"),
            403,
            "not_editable",
            {"source_entity_id": "src-1"},
        ),
        (lambda m: m.EntityVersionConflict(5), 409, "version_conflict", {"current_version": 5}),
        (
            lambda m: m.EntityNameInUse("other-id", "Hans", "Hans", True),
            409,
            "name_in_use",
            {
                "conflicting_entity_id": "other-id",
                "conflicting_name": "Hans",
                "name": "Hans",
                "conflicting_is_staged": True,
            },
        ),
        (lambda m: m.EntityNameInvalid("empty_name"), 422, "empty_name", {}),
        (lambda m: m.EntityNameInvalid("invalid_characters"), 422, "invalid_characters", {}),
        (lambda m: m.EntityNameInvalid("too_many_aliases"), 422, "too_many_aliases", {}),
        (lambda m: m.EntityStoreUnavailable(), 503, "store_unavailable", {}),
    ],
)
def test_patch_domain_errors_map_to_status_and_body(
    client, kg_store, monkeypatch, exc_factory, status_code, code, extra
):
    from services import entity_edit

    _patch_service(monkeypatch, entity_edit, "rename_entity", exc=exc_factory(entity_edit))
    resp = _patch(client, name="x")
    assert resp.status_code == status_code
    detail = resp.json()["detail"]
    # Web reads ApiError.code from detail.code (LUM-759): every domain error has both.
    assert detail["error"] == detail["code"] == code
    for k, v in extra.items():
        assert detail[k] == v


def test_patch_422_nothing_to_change_never_calls_service(client, kg_store, monkeypatch):
    from services import entity_edit

    def _boom(*a, **kw):
        raise AssertionError("service must not be called")

    monkeypatch.setattr(entity_edit, "rename_entity", _boom)
    resp = _patch(client, aliases_add=[], aliases_remove=[])
    assert resp.status_code == 422
    assert resp.json()["detail"] == {"error": "nothing_to_change", "code": "nothing_to_change"}


def test_patch_422_extra_field_and_51_aliases_are_pydantic(client, kg_store, monkeypatch):
    from services import entity_edit

    _patch_service(monkeypatch, entity_edit, "rename_entity", _row())
    extra = _patch(client, name="x", aliases=["a"])
    assert extra.status_code == 422
    assert isinstance(extra.json()["detail"], list)
    many = _patch(client, aliases_add=[f"a{i}" for i in range(51)])
    assert many.status_code == 422
    assert isinstance(many.json()["detail"], list)
    assert _patch(client, name="x", version=0).status_code == 422


# ── kind change (LUM-967) ────────────────────────────────────────────────────


def test_patch_kind_ok_returns_card_with_new_type(client, kg_store, monkeypatch):
    from services import entity_edit

    seen = {}

    def _fn(user, entity_id, **kw):
        seen.update(kw)
        return _row(entity_type="ORG", version=2)

    monkeypatch.setattr(entity_edit, "change_entity_kind", _fn)
    monkeypatch.setattr(entity_edit, "rename_entity", lambda *a, **k: pytest.fail("rename called"))
    resp = _patch(client, entity_type="ORG")
    assert resp.status_code == 200
    assert resp.json()["type"] == "ORG"
    assert resp.json()["version"] == 2
    assert seen == {"entity_type": "ORG", "version": 1}


@pytest.mark.parametrize(
    "extra", [{"name": "X"}, {"aliases_add": ["a"]}, {"aliases_remove": ["b"]}]
)
def test_patch_kind_with_other_change_422_one_change_at_a_time(
    client, kg_store, monkeypatch, extra
):
    from services import entity_edit

    def _boom(*a, **kw):
        raise AssertionError("service must not be called")

    monkeypatch.setattr(entity_edit, "change_entity_kind", _boom)
    monkeypatch.setattr(entity_edit, "rename_entity", _boom)
    resp = _patch(client, entity_type="ORG", **extra)
    assert resp.status_code == 422
    assert resp.json()["detail"] == {
        "error": "one_change_at_a_time",
        "code": "one_change_at_a_time",
    }


def test_patch_kind_invalid_422_invalid_kind(client, kg_store, monkeypatch):
    from services import entity_edit

    _patch_service(
        monkeypatch, entity_edit, "change_entity_kind", exc=entity_edit.EntityKindInvalid("x")
    )
    resp = _patch(client, entity_type="PLACE")
    assert resp.status_code == 422
    assert resp.json()["detail"] == {"error": "invalid_kind", "code": "invalid_kind"}


def test_patch_kind_name_in_use_409_has_conflicting_entity_type(client, kg_store, monkeypatch):
    from services import entity_edit

    exc = entity_edit.EntityNameInUse(
        "other-id", "Meier Pharmacy", "Meier Pharmacy", False, conflicting_entity_type="ORG"
    )
    _patch_service(monkeypatch, entity_edit, "change_entity_kind", exc=exc)
    resp = _patch(client, entity_type="ORG")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "name_in_use"
    assert detail["conflicting_entity_type"] == "ORG"
    assert detail["conflicting_entity_id"] == "other-id"


def test_patch_rename_name_in_use_409_conflicting_entity_type_null(client, kg_store, monkeypatch):
    from services import entity_edit

    exc = entity_edit.EntityNameInUse("other-id", "Hans", "Hans", False)
    _patch_service(monkeypatch, entity_edit, "rename_entity", exc=exc)
    resp = _patch(client, name="Hans")
    assert resp.status_code == 409
    assert resp.json()["detail"]["conflicting_entity_type"] is None


def test_patch_kind_too_long_422_validation(client, kg_store, monkeypatch):
    from services import entity_edit

    _patch_service(monkeypatch, entity_edit, "change_entity_kind", _row())
    resp = _patch(client, entity_type="X" * 33)
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], list)
    assert isinstance(_patch(client, entity_type="").json()["detail"], list)


def test_patch_kind_uses_the_same_origin_guarded_route():
    """The kind change is the existing PATCH route (CSRF guard, owner-only auth)."""
    from csrf import require_same_origin
    from routes.api_v1 import kg

    patch_routes = [r for r in kg.router.routes if getattr(r, "methods", None) == {"PATCH"}]
    assert len(patch_routes) == 1
    assert require_same_origin in [d.dependency for d in patch_routes[0].dependencies]


def test_patch_mounts_same_origin_and_router_requires_user():
    from authz import require_user
    from csrf import require_same_origin
    from routes.api_v1 import kg

    assert require_user in [d.dependency for d in kg.router.dependencies]
    patch_route = next(r for r in kg.router.routes if getattr(r, "methods", None) == {"PATCH"})
    assert require_same_origin in [d.dependency for d in patch_route.dependencies]


def test_card_includes_version(client, kg_store):
    _seed_alice(kg_store)
    kg_store.entities[0]["version"] = 4
    body = client.get(f"/api/v1/kg/entities/{_EID}").json()
    assert body["version"] == 4


# VERIFY-PLAN: added — behavioural 401 per new route (plan § Negative test cases),
# alongside the structural mount check above.
@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/api/v1/kg/entities", None),
        ("GET", f"/api/v1/kg/entities/{_EID}/mentions", None),
        ("PATCH", f"/api/v1/kg/entities/{_EID}", {"name": "X", "version": 1}),
    ],
)
def test_new_routes_401_unauthenticated(client, kg_store, monkeypatch, method, path, body):
    from services import entity_browser
    from services import entity_edit

    def _boom(*_a, **_k):
        raise AssertionError("service reached without a user")

    monkeypatch.setattr(entity_browser, "list_entities", _boom)
    monkeypatch.setattr(entity_browser, "list_mentions", _boom)
    monkeypatch.setattr(entity_edit, "rename_entity", _boom)
    monkeypatch.setenv("AUTH_SECRET", "test-kg-lum161-secret-do-not-use-0123456789")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    r = client.request(method, path, json=body)
    assert r.status_code == 401
