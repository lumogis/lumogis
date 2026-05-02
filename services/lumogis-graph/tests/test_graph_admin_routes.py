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
  * GET /kg/stop-entities returns an empty list when the file does not exist.
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
# /kg/stop-entities
# ---------------------------------------------------------------------------


def test_kg_stop_entities_get_returns_empty_when_file_missing(monkeypatch, tmp_path):
    """Stop file doesn't exist → empty list, source_path still returned."""
    import config

    nonexistent = tmp_path / "stop_entities.txt"
    monkeypatch.setattr(config, "get_stop_entities_path", lambda: str(nonexistent))

    r = _client_with_admin().get("/kg/stop-entities")
    assert r.status_code == 200
    body = r.json()
    assert body["phrases"] == []
    assert body["count"] == 0
    assert body["source_path"] == str(nonexistent)
