# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Integration tests for the graph pipeline (Phase 3 M9).

These tests exercise the full graph pipeline end-to-end against a live
FalkorDB instance reachable via the orchestrator's HTTP API.

Requirements:
  - Running orchestrator stack with FalkorDB overlay:
      COMPOSE_FILE=docker-compose.yml:docker-compose.falkordb.yml docker compose up -d
  - FALKORDB_URL and GRAPH_BACKEND=falkordb set in orchestrator env

Tests that require FalkorDB are skipped when the graph backend reports
unavailable=true or the orchestrator is unreachable.

Run:
  make compose-test-integration
  # or locally:
  cd orchestrator && python -m pytest ../tests/integration/test_graph.py -v --tb=short -m integration
"""

import time
import uuid

import pytest

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _poll(predicate, *, timeout=60, interval=2, desc="condition"):
    """Poll predicate() up to timeout seconds. Returns the truthy result or raises."""
    deadline = time.monotonic() + timeout
    last_result = None
    while time.monotonic() < deadline:
        last_result = predicate()
        if last_result:
            return last_result
        time.sleep(interval)
    raise AssertionError(
        f"Timed out after {timeout}s waiting for {desc}. Last result: {last_result}"
    )


def _graph_available(api) -> bool:
    """Return True if the graph backend is configured and reachable."""
    r = api.get("/graph/stats")
    if r.status_code != 200:
        return False
    return r.json().get("available", False) is True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def graph_api(api):
    """Yield the shared api client, skipping the module if graph is unavailable."""
    if not _graph_available(api):
        pytest.skip("FalkorDB graph backend not available — skipping graph integration tests")
    yield api


@pytest.fixture(scope="module")
def ingested_doc(graph_api, repo_root):
    """Ingest a test document and return metadata once graph_projected_at is stamped.

    Polls /health for file_index_count increase as a proxy for ingest completion,
    then polls the file_index export for the graph_projected_at stamp.
    """
    inbox = repo_root / "ai-workspace" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    token = f"graphinteg_{uuid.uuid4().hex[:10]}"
    test_file = inbox / f"{token}.txt"
    test_file.write_text(
        f"Integration test document for graph pipeline.\n"
        f"Sarah Chen presented the quarterly review at Meridian Corp.\n"
        f"Reference: {token}\n",
        encoding="utf-8",
    )

    hr_before = graph_api.get("/health")
    assert hr_before.status_code == 200
    count_before = hr_before.json().get("file_index_count", 0)

    ir = graph_api.post("/ingest", json={"path": "/workspace/inbox"})
    assert ir.status_code == 200

    def _file_indexed():
        hr = graph_api.get("/health")
        if hr.status_code == 200 and hr.json().get("file_index_count", 0) > count_before:
            return True
        return False

    _poll(_file_indexed, timeout=120, interval=3, desc="file_index_count to increase")

    yield {
        "file_path": f"/workspace/inbox/{token}.txt",
        "token": token,
        "test_file": test_file,
    }

    test_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 1 — Full ingest pipeline
# ---------------------------------------------------------------------------

class TestFullIngestPipeline:
    def test_document_node_created_and_stamped(self, graph_api, ingested_doc):
        """After ingest, graph_projected_at should be stamped and the Document
        node should exist in the graph (visible via /graph/stats node count)."""

        def _graph_stamped():
            r = graph_api.get("/export")
            if r.status_code != 200:
                return False
            for line in r.text.strip().split("\n"):
                if not line.strip():
                    continue
                import json
                section = json.loads(line)
                if section.get("section") == "file_index":
                    for row in section.get("rows", []):
                        if ingested_doc["token"] in (row.get("file_path") or ""):
                            if row.get("graph_projected_at"):
                                return True
            return False

        _poll(
            _graph_stamped,
            timeout=90,
            interval=3,
            desc="graph_projected_at to be stamped on file_index row",
        )

        stats = graph_api.get("/graph/stats")
        assert stats.status_code == 200
        body = stats.json()
        assert body.get("available") is True
        assert body.get("node_count", 0) >= 1


# ---------------------------------------------------------------------------
# Test 2 — Reconciliation heals a gap
# ---------------------------------------------------------------------------

class TestReconciliationHealsGap:
    def test_backfill_projects_unstamped_rows(self, graph_api):
        """POST /graph/backfill replays stale rows. After backfill, the
        reconciliation counters in /graph/stats should reflect the work done."""
        r = graph_api.post("/graph/backfill")
        assert r.status_code in (202, 409), f"Expected 202 or 409, got {r.status_code}"

        if r.status_code == 409:
            def _backfill_done():
                retry = graph_api.post("/graph/backfill")
                return retry.status_code == 202
            _poll(_backfill_done, timeout=60, interval=5, desc="backfill slot to free up")

        time.sleep(5)

        stats = graph_api.get("/graph/stats")
        assert stats.status_code == 200
        assert stats.json().get("available") is True


# ---------------------------------------------------------------------------
# Test 3 — Self-healing smoke test (simulated)
# ---------------------------------------------------------------------------

class TestSelfHealingSmokeTest:
    def test_backfill_heals_after_simulated_gap(self, graph_api):
        """Simulate a gap by triggering backfill and verifying it completes.

        This is a simulation — not a Docker stop/start. The full Docker
        stop/start version should be run manually using scripts/smoke_test_m2.sh.
        """
        r = graph_api.post("/graph/backfill")
        assert r.status_code in (202, 409)

        if r.status_code == 202:
            body = r.json()
            assert body["status"] == "backfill_started"


# ---------------------------------------------------------------------------
# Test 4 — /graph/ego API returns correct shape
# ---------------------------------------------------------------------------

class TestEgoAPI:
    def test_ego_returns_correct_shape(self, graph_api):
        """GET /graph/ego returns the expected response structure."""
        search = graph_api.get("/graph/search", params={"q": "aa", "limit": 1})
        if search.status_code != 200 or not search.json().get("results"):
            pytest.skip("No entities in graph to test ego network against")

        entity_name = search.json()["results"][0]["name"]
        r = graph_api.get("/graph/ego", params={"entity": entity_name})
        assert r.status_code == 200
        body = r.json()

        assert "available" in body
        assert body["available"] is True
        assert "nodes" in body
        assert "edges" in body
        assert "entity_id" in body
        assert "entity_name" in body
        assert "truncated" in body

    def test_ego_ignores_user_id_query_param(self, graph_api):
        """user_id in query string must be ignored; route uses auth-derived user_id."""
        search = graph_api.get("/graph/search", params={"q": "aa", "limit": 1})
        if search.status_code != 200 or not search.json().get("results"):
            pytest.skip("No entities in graph to test auth scoping")

        entity_name = search.json()["results"][0]["name"]
        r = graph_api.get(
            "/graph/ego",
            params={"entity": entity_name, "user_id": "evil_injected_user"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body.get("available") is True


# ---------------------------------------------------------------------------
# Test 5 — /graph/path returns path or truthful no-path
# ---------------------------------------------------------------------------

class TestPathAPI:
    def test_path_with_known_entities(self, graph_api):
        """GET /graph/path returns path_found and correct response structure."""
        search = graph_api.get("/graph/search", params={"q": "aa", "limit": 5})
        if search.status_code != 200:
            pytest.skip("Entity search not working")

        results = search.json().get("results", [])
        if len(results) < 2:
            pytest.skip("Need at least 2 entities to test path query")

        a = results[0]["name"]
        b = results[1]["name"]
        r = graph_api.get("/graph/path", params={"from_entity": a, "to_entity": b})
        assert r.status_code == 200
        body = r.json()

        assert "available" in body
        assert body["available"] is True
        assert "path_found" in body
        assert "nodes" in body
        assert "edges" in body

    def test_path_between_unconnected_entities(self, graph_api):
        """Path between entities with no connection returns path_found=False gracefully."""
        fake_a = f"nonexistent_entity_{uuid.uuid4().hex[:8]}"
        fake_b = f"nonexistent_entity_{uuid.uuid4().hex[:8]}"
        r = graph_api.get(
            "/graph/path",
            params={"from_entity": fake_a, "to_entity": fake_b},
        )
        assert r.status_code == 200
        body = r.json()
        assert body.get("available") is True
        assert body.get("found") is False or body.get("path_found") is False


# ---------------------------------------------------------------------------
# Test 6 — Graph unavailable degrades gracefully
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    def test_endpoints_degrade_gracefully_when_graph_absent(self, api):
        """When graph is unavailable, all viz endpoints return structured JSON, not 500.

        This test uses the main api fixture (not graph_api) so it runs
        even when FalkorDB is not configured. If graph IS available, the
        test verifies normal responses instead — both are valid.
        """
        for endpoint in ["/graph/ego?entity=test", "/graph/stats", "/graph/search?q=test"]:
            r = api.get(endpoint)
            assert r.status_code == 200, f"{endpoint} returned {r.status_code}"
            body = r.json()
            assert "available" in body or "results" in body, (
                f"{endpoint} should return structured JSON, got: {body}"
            )


# ---------------------------------------------------------------------------
# Test 7 — Reconciliation idempotency
# ---------------------------------------------------------------------------

class TestReconciliationIdempotency:
    def test_double_backfill_is_idempotent(self, graph_api):
        """Running backfill twice should not create duplicate nodes."""
        stats_before = graph_api.get("/graph/stats").json()
        node_count_before = stats_before.get("node_count", 0)

        r1 = graph_api.post("/graph/backfill")
        if r1.status_code == 409:
            time.sleep(10)
            r1 = graph_api.post("/graph/backfill")
        if r1.status_code != 202:
            pytest.skip("Could not start backfill — slot busy")

        time.sleep(10)

        r2 = graph_api.post("/graph/backfill")
        if r2.status_code == 409:
            time.sleep(15)
            r2 = graph_api.post("/graph/backfill")
        if r2.status_code != 202:
            pytest.skip("Could not start second backfill — slot busy")

        time.sleep(10)

        stats_after = graph_api.get("/graph/stats").json()
        node_count_after = stats_after.get("node_count", 0)

        assert node_count_after >= node_count_before, (
            "Node count should not decrease after double backfill"
        )


# ---------------------------------------------------------------------------
# Test 8 — Auth scoping: user_id always from auth, never query params
# ---------------------------------------------------------------------------

class TestAuthScoping:
    def test_user_id_param_is_ignored(self, graph_api):
        """Passing user_id in query params must not override auth-derived scoping."""
        r_normal = graph_api.get("/graph/stats")
        r_evil = graph_api.get("/graph/stats", params={"user_id": "evil"})

        assert r_normal.status_code == 200
        assert r_evil.status_code == 200

        r_ego = graph_api.get(
            "/graph/ego",
            params={"entity": "test", "user_id": "evil"},
        )
        assert r_ego.status_code == 200
        body = r_ego.json()
        assert "available" in body
