# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for `routes/graph_admin_routes.py`.

Contract under test (a focused subset — the routes are ports of Core's
`/kg/*` endpoints which already have full coverage in
`orchestrator/tests/test_kg_settings.py` and `test_graph_mgm.py`):

  * GET /kg/settings returns all known knobs with default values when
    Postgres is empty.
  * POST /kg/settings is open when GRAPH_ADMIN_TOKEN is unset (dev default)
    and 403 with bad token when set.
  * GET /kg/job-status returns nulls when the metadata store is empty.
  * POST /kg/trigger-weekly returns 202 when no dedup job is running.
  * POST /kg/trigger-weekly returns 409 when a dedup job is already running.
  * GET/POST /kg/stop-entities return 410 — the list is managed by Core (LUM-952).
  * POST /kg/orphan-gc and /kg/recount-cooccurrence: dry run by default, token
    required for a destructive run, 503 without a graph store (LUM-952).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client_with_admin() -> TestClient:
    from routes.graph_admin_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# /kg/settings
# ---------------------------------------------------------------------------


def test_kg_settings_get_returns_all_settings():
    """Empty Postgres → every known knob present, source='default'."""
    r = _client_with_admin().get("/kg/settings")
    assert r.status_code == 200
    body = r.json()
    assert "settings" in body
    keys = {s["key"] for s in body["settings"]}
    assert "entity_quality_lower" in keys
    assert "graph_edge_quality_threshold" in keys
    assert "decay_half_life_relates_to" in keys
    for s in body["settings"]:
        assert s["source"] in {"default", "db"}


def test_kg_settings_post_open_when_admin_token_unset(monkeypatch):
    """GRAPH_ADMIN_TOKEN unset → write endpoints accept any caller (dev default)."""
    monkeypatch.delenv("GRAPH_ADMIN_TOKEN", raising=False)
    r = _client_with_admin().post(
        "/kg/settings",
        json={"settings": [{"key": "entity_quality_lower", "value": "0.42"}]},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert "entity_quality_lower" in r.json()["updated"]


def test_kg_settings_post_rejects_wrong_and_non_ascii_admin_token(monkeypatch):
    monkeypatch.setenv("GRAPH_ADMIN_TOKEN", "admin-token")
    for token in (b"wrong-token", "\xe9t\xe9".encode("latin-1")):
        r = _client_with_admin().post(
            "/kg/settings",
            headers={"X-Graph-Admin-Token": token},
            json={"settings": [{"key": "entity_quality_lower", "value": "0.42"}]},
        )
        assert r.status_code == 403, token


def test_kg_settings_post_requires_admin_token_when_set(monkeypatch):
    monkeypatch.setenv("GRAPH_ADMIN_TOKEN", "admin-token")

    r1 = _client_with_admin().post(
        "/kg/settings",
        json={"settings": [{"key": "entity_quality_lower", "value": "0.42"}]},
    )
    assert r1.status_code == 403

    r2 = _client_with_admin().post(
        "/kg/settings",
        headers={"X-Graph-Admin-Token": "admin-token"},
        json={"settings": [{"key": "entity_quality_lower", "value": "0.42"}]},
    )
    assert r2.status_code == 200


def test_kg_settings_post_rejects_unknown_key(monkeypatch):
    monkeypatch.delenv("GRAPH_ADMIN_TOKEN", raising=False)
    r = _client_with_admin().post(
        "/kg/settings",
        json={"settings": [{"key": "totally_made_up_key", "value": "1"}]},
    )
    assert r.status_code == 400
    assert "unknown key" in r.json()["detail"]


# ---------------------------------------------------------------------------
# /kg/job-status
# ---------------------------------------------------------------------------


def test_kg_job_status_returns_nulls_when_empty():
    """Empty MetadataStore → all fields default to None / sane zeros."""
    r = _client_with_admin().get("/kg/job-status")
    assert r.status_code == 200
    body = r.json()
    assert body["reconciliation"]["last_run"] is None
    assert body["weekly_quality"]["last_run"] is None
    assert body["deduplication"]["last_run"] is None
    assert body["deduplication"]["running"] is False


# ---------------------------------------------------------------------------
# /kg/trigger-weekly
# ---------------------------------------------------------------------------


def test_kg_trigger_weekly_returns_202_when_no_dedup_running(monkeypatch):
    """Empty fetch_one for in-progress check → 202 + status='started'."""
    monkeypatch.delenv("GRAPH_ADMIN_TOKEN", raising=False)
    r = _client_with_admin().post("/kg/trigger-weekly")
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "started"


def test_kg_trigger_weekly_returns_409_when_dedup_already_running(mock_metadata_store, monkeypatch):
    """If `deduplication_runs` has an unfinished row → 409 with operator-friendly detail."""
    monkeypatch.delenv("GRAPH_ADMIN_TOKEN", raising=False)
    mock_metadata_store._seed_fetch_one({"run_id": "in-flight-uuid"})

    r = _client_with_admin().post("/kg/trigger-weekly")
    assert r.status_code == 409
    assert "already running" in r.json()["detail"]


def test_kg_trigger_weekly_requires_admin_token_when_set(monkeypatch):
    monkeypatch.setenv("GRAPH_ADMIN_TOKEN", "admin-token")
    r = _client_with_admin().post("/kg/trigger-weekly")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# /kg/stop-entities — managed by Core (LUM-952)
# ---------------------------------------------------------------------------


def test_stop_entities_get_is_gone_with_core_pointer():
    r = _client_with_admin().get("/kg/stop-entities")
    assert r.status_code == 410
    assert "/graph/mgm" in r.json()["detail"] and "Core" in r.json()["detail"]


def test_stop_entities_post_is_gone(monkeypatch):
    monkeypatch.delenv("GRAPH_ADMIN_TOKEN", raising=False)
    r = _client_with_admin().post("/kg/stop-entities", json={"action": "add", "phrase": "x"})
    assert r.status_code == 410


def test_stop_entities_post_still_requires_token_when_set(monkeypatch):
    monkeypatch.setenv("GRAPH_ADMIN_TOKEN", "secret")
    r = _client_with_admin().post("/kg/stop-entities", json={})
    assert r.status_code == 403


def test_graph_service_has_no_stop_list_code():
    """The graph service never filters entities; Core owns the only list."""
    import importlib.util

    import config

    assert importlib.util.find_spec("quality.entity_quality") is None
    for name in ("get_stop_entities_path", "get_stop_entity_set", "_resolve_config_file"):
        assert not hasattr(config, name), name


# ---------------------------------------------------------------------------
# POST /kg/orphan-gc and /kg/recount-cooccurrence (LUM-952)
# ---------------------------------------------------------------------------


def _spy(monkeypatch, name):
    from graph import reconcile

    import config

    calls: list[dict] = []

    def fake(*args, **kwargs):
        calls.append(kwargs)
        return {"orphans_found": 0, "deleted_ok": 0, "refused": False, "edges_changed": 0}

    monkeypatch.setattr(reconcile, name, fake)
    monkeypatch.setattr(config, "get_graph_store", lambda bank=None: object())
    return calls


def test_orphan_gc_route_defaults_to_dry_run(monkeypatch):
    monkeypatch.delenv("GRAPH_ADMIN_TOKEN", raising=False)
    calls = _spy(monkeypatch, "garbage_collect_orphan_nodes")
    r = _client_with_admin().post("/kg/orphan-gc", json={})
    assert r.status_code == 200
    assert calls == [{"limit": None, "dry_run": True, "force": False}]


def test_recount_route_defaults_to_dry_run(monkeypatch):
    monkeypatch.delenv("GRAPH_ADMIN_TOKEN", raising=False)
    calls = _spy(monkeypatch, "recount_cooccurrence")
    r = _client_with_admin().post("/kg/recount-cooccurrence", json={})
    assert r.status_code == 200
    assert calls == [{"user_id": None, "dry_run": True}]


def test_destructive_routes_refuse_without_configured_token(monkeypatch):
    monkeypatch.delenv("GRAPH_ADMIN_TOKEN", raising=False)
    for route, fn in (
        ("/kg/orphan-gc", "garbage_collect_orphan_nodes"),
        ("/kg/recount-cooccurrence", "recount_cooccurrence"),
    ):
        calls = _spy(monkeypatch, fn)
        r = _client_with_admin().post(route, json={"dry_run": False})
        assert r.status_code == 403 and "GRAPH_ADMIN_TOKEN" in r.json()["detail"]
        assert calls == []


def test_destructive_run_allowed_with_token(monkeypatch):
    monkeypatch.setenv("GRAPH_ADMIN_TOKEN", "secret")
    calls = _spy(monkeypatch, "garbage_collect_orphan_nodes")
    r = _client_with_admin().post(
        "/kg/orphan-gc",
        json={"dry_run": False, "force": True, "limit": 10},
        headers={"X-Graph-Admin-Token": "secret"},
    )
    assert r.status_code == 200
    assert calls == [{"limit": 10, "dry_run": False, "force": True}]


def test_orphan_gc_route_requires_token_when_configured(monkeypatch):
    monkeypatch.setenv("GRAPH_ADMIN_TOKEN", "secret")
    calls = _spy(monkeypatch, "garbage_collect_orphan_nodes")
    r = _client_with_admin().post("/kg/orphan-gc", json={})
    assert r.status_code == 403 and calls == []


def test_orphan_gc_route_rejects_bad_body(monkeypatch):
    monkeypatch.delenv("GRAPH_ADMIN_TOKEN", raising=False)
    _spy(monkeypatch, "garbage_collect_orphan_nodes")
    r = _client_with_admin().post("/kg/orphan-gc", json={"limit": 0})
    assert r.status_code == 422


def test_routes_return_503_without_graph_store(monkeypatch):
    import config

    monkeypatch.delenv("GRAPH_ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(config, "get_graph_store", lambda bank=None: None)
    for route in ("/kg/orphan-gc", "/kg/recount-cooccurrence"):
        assert _client_with_admin().post(route, json={}).status_code == 503
