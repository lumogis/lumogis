# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``/api/v1/kg`` delete, hide and names-Lumogis-won't-learn routes (LUM-966).

The service is mocked; this file pins the HTTP contract (status codes, error
codes, validation, CSRF, auth) and the Hide filter on each Core read. What the
service and the SQL do is proven in ``tests/test_entity_delete.py`` and
``tests/integration/test_entity_delete_live.py``. Runs with ``GRAPH_MODE=disabled``
(the unit-test default): the KG routes read Postgres only in every mode.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from datetime import timezone
from unittest.mock import patch

import pytest
import routes.api_v1.kg as kgmod
from auth import UserContext
from authz import require_user
from csrf import require_same_origin
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config as _config
from services import entity_edit

A = str(uuid.uuid4())
ORIGIN = {"Origin": "http://testserver"}
NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(kgmod.router)
    ctx = UserContext(user_id="alice", is_authenticated=True, role="user", allows_shared=True)
    app.dependency_overrides[require_user] = lambda: ctx
    monkeypatch.setattr(kgmod, "get_user", lambda request: ctx)
    monkeypatch.setattr(kgmod, "_effective_allows_shared", lambda u: True)
    monkeypatch.setattr(kgmod, "_fetch_shared_entity_source_ids", lambda *a, **k: set())
    return TestClient(app)


def _outcome(**over):
    base = dict(
        entity_id=A,
        blocked_names=["Total", "Total GmbH"],
        not_blocked=[{"name": "Tot", "reason": "used_by_other_entry"}],
        household_copy_removed=True,
        merges_removed=2,
    )
    base.update(over)
    return entity_edit.DeleteOutcome(**base)


def _delete(client, **kw):
    return client.delete(f"/api/v1/kg/entities/{A}", params={"version": 3}, headers=ORIGIN, **kw)


# ── delete ───────────────────────────────────────────────────────────────────


def test_R1_delete_200_shape(client):
    with patch.object(entity_edit, "delete_entry", return_value=_outcome()) as m:
        resp = _delete(client)
    assert resp.status_code == 200
    assert resp.json() == {
        "deleted": True,
        "entity_id": A,
        "blocked_names": ["Total", "Total GmbH"],
        "not_blocked": [{"name": "Tot", "reason": "used_by_other_entry"}],
        "household_copy_removed": True,
        "merges_removed": 2,
    }
    assert m.call_args.args[1] == A and m.call_args.kwargs == {"version": 3}


def test_R2_version_is_required_and_not_negative(client):
    assert client.delete(f"/api/v1/kg/entities/{A}", headers=ORIGIN).status_code == 422
    resp = client.delete(f"/api/v1/kg/entities/{A}", params={"version": -1}, headers=ORIGIN)
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "exc,status,code,extra",
    [
        (entity_edit.EntityNotFound(A), 404, "entity_not_found", {}),
        (entity_edit.EntityNotOwner(A), 403, "not_owner", {}),
        (
            entity_edit.EntityNotEditable("src-1"),
            403,
            "not_editable",
            {"source_entity_id": "src-1"},
        ),
        (entity_edit.EntityVersionConflict(9), 409, "version_conflict", {"current_version": 9}),
        (entity_edit.EntityStoreUnavailable("x"), 503, "store_unavailable", {}),
    ],
)
def test_R3_delete_errors(client, exc, status, code, extra):
    with patch.object(entity_edit, "delete_entry", side_effect=exc):
        resp = _delete(client)
    assert resp.status_code == status
    detail = resp.json()["detail"]
    assert detail["code"] == code and detail["error"] == code
    for k, v in extra.items():
        assert detail[k] == v


# ── hide / unhide ────────────────────────────────────────────────────────────


def test_R6_hide_200_and_own_entry_422(client):
    with patch.object(entity_edit, "hide_entry", return_value="key") as m:
        resp = client.post(f"/api/v1/kg/entities/{A}/hide", headers=ORIGIN)
    assert resp.status_code == 200
    assert resp.json() == {"entity_id": A, "hidden": True}
    assert m.call_args.args[1] == A
    with patch.object(entity_edit, "hide_entry", side_effect=entity_edit.EntityHideOwn(A)):
        resp = client.post(f"/api/v1/kg/entities/{A}/hide", headers=ORIGIN)
    assert resp.status_code == 422 and resp.json()["detail"]["code"] == "own_entry"
    with patch.object(entity_edit, "hide_entry", side_effect=entity_edit.EntityNotFound(A)):
        assert client.post(f"/api/v1/kg/entities/{A}/hide", headers=ORIGIN).status_code == 404


def test_R7_unhide_is_idempotent(client):
    with patch.object(entity_edit, "unhide_entry", return_value=True):
        resp = client.delete(f"/api/v1/kg/entities/{A}/hide", headers=ORIGIN)
    assert resp.status_code == 200 and resp.json() == {"entity_id": A, "hidden": False}
    with patch.object(entity_edit, "unhide_entry", side_effect=entity_edit.EntityHideOwn(A)):
        assert client.delete(f"/api/v1/kg/entities/{A}/hide", headers=ORIGIN).status_code == 422


# ── name rules ───────────────────────────────────────────────────────────────


def _rules(**over):
    base = {
        "blocked": [
            {
                "name_norm": "total",
                "display_name": "Total",
                "entity_type": "PERSON",
                "document_count": 3,
                "created_at": NOW,
            }
        ],
        "corrections": [
            {"name_norm": "acme", "display_name": "Acme", "from_type": "PERSON", "to_type": "ORG"}
        ],
        "hidden": [{"entity_id": A, "name": "Bob's note", "type": "CONCEPT", "hidden_at": NOW}],
        "blocked_total": 1,
        "corrections_total": 1,
        "hidden_total": 1,
    }
    base.update(over)
    return base


def test_R8_get_name_rules_returns_the_three_lists_for_the_session_user(client):
    with patch.object(entity_edit, "list_name_rules", return_value=_rules()) as m:
        resp = client.get("/api/v1/kg/name-rules", params={"user_id": "mallory"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["blocked"][0]["display_name"] == "Total"
    assert body["corrections"][0]["to_type"] == "ORG"
    assert body["hidden"][0]["entity_id"] == A
    assert m.call_args.args[0].user_id == "alice"  # R14: never from the request


def test_R18_the_list_reports_uncapped_totals(client):
    rules = _rules(blocked=_rules()["blocked"] * 500, blocked_total=501)
    with patch.object(entity_edit, "list_name_rules", return_value=rules):
        body = client.get("/api/v1/kg/name-rules").json()
    assert len(body["blocked"]) == 500 and body["blocked_total"] == 501


def test_R9_remove_name_rule(client):
    result = {
        "removed": True,
        "restored_corrections": [
            {"name_norm": "total", "display_name": "Total", "from_type": "ORG", "to_type": "PERSON"}
        ],
        "reread_documents": [{"document_id": 7, "title": "a.pdf"}],
        "reread_more": 4,
    }
    body = {"kind": "block", "name_norm": "total", "entity_type": "PERSON"}
    with patch.object(entity_edit, "remove_name_rule", return_value=result) as m:
        resp = client.post("/api/v1/kg/name-rules/remove", json=body, headers=ORIGIN)
    assert resp.status_code == 200
    assert m.call_args.args[0].user_id == "alice"  # R14: the session user
    assert resp.json()["reread_documents"] == [{"document_id": 7, "title": "a.pdf"}]
    assert resp.json()["reread_more"] == 4
    assert m.call_args.kwargs == {"name_norm": "total", "entity_type": "PERSON", "kind": "block"}

    # R14: a body naming a user is refused outright (extra fields forbidden), and the
    # service is never reached. VERIFY-PLAN: added.
    with patch.object(entity_edit, "remove_name_rule") as never:
        resp = client.post(
            "/api/v1/kg/name-rules/remove",
            json={**body, "user_id": "mallory"},
            headers=ORIGIN,
        )
    assert resp.status_code == 422
    never.assert_not_called()

    with patch.object(
        entity_edit, "remove_name_rule", side_effect=entity_edit.NameRuleNotFound("x")
    ):
        resp = client.post("/api/v1/kg/name-rules/remove", json=body, headers=ORIGIN)
    assert resp.status_code == 404 and resp.json()["detail"]["code"] == "rule_not_found"

    for bad in ({**body, "name_norm": ""}, {**body, "kind": "other"}, {**body, "user_id": "bob"}):
        assert (
            client.post("/api/v1/kg/name-rules/remove", json=bad, headers=ORIGIN).status_code == 422
        )


# ── CSRF and auth on every new route ─────────────────────────────────────────


def _new_routes():
    out = []
    for r in kgmod.router.routes:
        if r.path.endswith(("/hide", "/name-rules", "/name-rules/remove")) or (
            r.path == "/api/v1/kg/entities/{entity_id}" and "DELETE" in r.methods
        ):
            out.append(r)
    return out


def test_R4_every_new_write_route_needs_same_origin(client):
    routes = _new_routes()
    assert len(routes) == 5  # DELETE entity, POST/DELETE hide, GET name-rules, POST remove
    for route in routes:
        writes = bool(route.methods & {"POST", "DELETE"})
        has = require_same_origin in [d.dependency for d in route.dependencies]
        assert has == writes, (route.path, route.methods)
    # The check itself (it bypasses with AUTH_ENABLED=false / no pinned public
    # origin, like the unit environment here) is covered by test_csrf*.py; what
    # this pins is that every mutating route mounts it and no read does.


def test_R5_every_new_route_sits_behind_require_user():
    assert require_user in [d.dependency for d in kgmod.router.dependencies]


def test_R5b_unauthenticated_requests_are_refused_when_auth_is_on(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    app = FastAPI()
    app.include_router(kgmod.router)
    c = TestClient(app)
    assert c.get("/api/v1/kg/name-rules").status_code in (401, 403)
    assert c.delete(
        f"/api/v1/kg/entities/{A}", params={"version": 1}, headers=ORIGIN
    ).status_code in (401, 403)


# ── Hide filters on the other reads ──────────────────────────────────────────


class RecordingStore:
    def __init__(self):
        self.log: list[tuple[str, tuple]] = []

    def fetch_one(self, sql, params=None):
        s = " ".join(sql.split())
        self.log.append((s, tuple(params or ())))
        if "FROM entity_hidden" in s and s.startswith("SELECT 1 AS x"):
            return {"x": 1}
        if s.startswith("SELECT entity_id, name, entity_type"):
            return {
                "entity_id": A,
                "name": "Acme",
                "entity_type": "ORG",
                "aliases": [],
                "context_tags": [],
                "mention_count": 1,
                "scope": "shared",
                "user_id": "bob",
                "published_from": "99999999-9999-4999-9999-999999999999",
                "version": 1,
            }
        if s.startswith("SELECT entity_id FROM entities"):
            return {"entity_id": A}
        return None

    def fetch_all(self, sql, params=None):
        self.log.append((" ".join(sql.split()), tuple(params or ())))
        return []


@pytest.fixture
def store():
    ms = RecordingStore()
    _config._instances["metadata_store"] = ms
    yield ms
    _config._instances.clear()


def test_R10_the_full_card_reports_hidden(client, store):
    body = client.get(f"/api/v1/kg/entities/{A}").json()
    assert body["hidden"] is True
    lookup = [(s, p) for s, p in store.log if s.startswith("SELECT 1 AS x FROM entity_hidden")]
    assert lookup[0][1] == (
        "alice",
        "99999999-9999-4999-9999-999999999999",
    )  # the owner's source row


def test_R12_search_carries_the_hide_clause(client, store):
    client.get("/api/v1/kg/search", params={"q": "ac"})
    (sql, params) = [(s, p) for s, p in store.log if "name ILIKE" in s][0]
    assert "NOT EXISTS (SELECT 1 FROM entity_hidden h WHERE h.user_id = %s" in sql
    assert params.count("alice") >= 3


def test_R13_related_carries_the_hide_clause(client, store):
    client.get(f"/api/v1/kg/entities/{A}/related")
    (sql, params) = [(s, p) for s, p in store.log if "FROM edge_scores" in s][0]
    assert "NOT EXISTS (SELECT 1 FROM entity_hidden h WHERE h.user_id = %s" in sql
    assert "coalesce(e.published_from, e.entity_id)" in sql


def test_R11_the_browser_list_and_its_counts_carry_the_hide_clause(store):
    from services import entity_browser

    entity_browser.list_entities(UserContext(user_id="alice", allows_shared=True))
    sqls = [s for s, _p in store.log if "FROM entities e" in s]
    assert len(sqls) == 2  # the items and the per-kind counts
    for sql in sqls:
        assert "NOT EXISTS (SELECT 1 FROM entity_hidden h WHERE h.user_id = %s" in sql
        assert "coalesce(e.published_from, e.entity_id)" in sql


def test_R15_lookup_and_search_by_name_carry_the_hide_clause(store):
    from services import entities

    entities.lookup_by_name("Acme", "alice")
    entities.search_by_name("Acme", user_id="alice")
    sqls = [(s, p) for s, p in store.log if "FROM entities" in s]
    assert len(sqls) >= 2
    for sql, params in sqls:
        assert "NOT EXISTS (SELECT 1 FROM entity_hidden h" in sql
        assert params.count("alice") >= 2


def test_R16_the_dashboard_entities_panel_carries_the_hide_clause(store):
    import routes.data as data

    class Req:
        pass

    with patch.object(
        data, "get_user", return_value=UserContext(user_id="alice", allows_shared=True)
    ):
        data.list_entities(Req(), type=None, scope=None, limit=10, offset=0)
    (sql, params) = [(s, p) for s, p in store.log if "FROM entities" in s][0]
    assert "NOT EXISTS (SELECT 1 FROM entity_hidden h" in sql
    assert params.count("alice") >= 2


def test_R17_briefing_and_wow_state_carry_the_hide_clause(store):
    from services import wow_state

    wow_state.count_visible_entities("alice")
    wow_state.list_top_entities("alice")
    sqls = [(s, p) for s, p in store.log if "FROM entities" in s]
    assert len(sqls) == 2
    for sql, params in sqls:
        assert "NOT EXISTS (SELECT 1 FROM entity_hidden h" in sql
        assert params.count("alice") >= 2
