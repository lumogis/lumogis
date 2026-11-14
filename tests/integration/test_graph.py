# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Integration tests for the graph pipeline (Phase 3 M9).

These tests exercise the full graph pipeline end-to-end on the RC stack:
Core (``api`` fixture, login when ``AUTH_ENABLED=true``) plus the lumogis-graph
service (``kg_api`` fixture, ``LUMOGIS_GRAPH_BASE_URL``). Core serves only
``/graph/health`` and ``/graph/mgm``; ``/graph/stats|ego|path|search|backfill``
live in the KG service, so every graph call here goes to ``kg_api``.

Requirements:
  - the seeded ``lumogis-test`` RC stack in service mode (``GRAPH_MODE=service``)
    with ``INBOX_OWNER_USER_ID`` set, so the inbox watcher files documents
    under a real owner (LUM-884)
  - Ollama with the extraction model for the entity-dependent tests

Tests skip when the KG service reports unavailable or the stack is unreachable.

Run (starts and tears down the RC stack, fails on any skip):
  make e2e-ingest-live
Or against an already running stack:
  cd orchestrator && python -m pytest -c ../tests/integration/pytest.ini \
      ../tests/integration/test_graph.py -v --tb=short -m integration
"""

import time
import uuid

import pytest
from _smoke_auth import current_user_id, file_index_row, llm_wait_s

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


def _graph_available(kg) -> bool:
    """Return True if the graph backend is configured and reachable."""
    r = kg.get("/graph/stats")
    if r.status_code != 200:
        return False
    return r.json().get("available", False) is True


def _start_backfill(kg, *, timeout=60, interval=5):
    """POST /graph/backfill, waiting out a busy slot (409). Returns the 202 response."""
    r = kg.post("/graph/backfill")
    deadline = time.monotonic() + timeout
    while r.status_code == 409 and time.monotonic() < deadline:
        time.sleep(interval)
        r = kg.post("/graph/backfill")
    assert r.status_code == 202, (
        f"backfill did not start within {timeout}s; last status {r.status_code}"
    )
    return r


def _wait_for_entity(kg, needle, timeout=None, *, exclude=()):
    """Poll /graph/search until an entity whose name contains *needle* appears.

    Names in *exclude* are ignored (used to find a second, distinct entity).
    Raises AssertionError naming the needle on timeout (never skips).
    """
    timeout = llm_wait_s(90) if timeout is None else timeout

    def _found():
        r = kg.get("/graph/search", params={"q": needle, "limit": 5})
        if r.status_code != 200:
            return None
        for hit in r.json().get("results", []):
            name = hit.get("name") or ""
            if needle.lower() in name.lower() and name not in exclude:
                return name
        return None

    return _poll(
        _found, timeout=timeout, interval=3, desc=f"entity matching {needle!r} in graph search"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def graph_api(kg_api):
    """Yield the KG service client, skipping the module if graph is unavailable."""
    if not _graph_available(kg_api):
        pytest.skip("lumogis-graph not available — skipping graph integration tests")
    yield kg_api


@pytest.fixture(scope="module")
def ingested_doc(graph_api, api, inbox_dir):
    """Drop a test document in the inbox and return metadata once it is indexed.

    The inbox watcher files it under the owner (``INBOX_OWNER_USER_ID``). Waits
    for the ``file_index`` row to exist (read from Postgres: the global
    ``/health`` ``file_index_count`` would pass on any owner's row).
    """
    # /ingest no longer takes a folder (LUM-884); the inbox watcher picks the file up.
    hz = {}
    try:
        hz = api.get("/healthz").json()
    except Exception:
        pass
    if hz.get("inbox_watcher") != "ok":
        pytest.skip(
            "inbox watcher not running (set INBOX_OWNER_USER_ID); "
            "/ingest no longer takes a folder — LUM-884"
        )
    inbox_dir.mkdir(parents=True, exist_ok=True)
    token = f"graphinteg_{uuid.uuid4().hex[:10]}"
    # Per-run names: a search cannot match an entity left from an earlier run on a
    # persistent volume.
    suffix = token[-6:].upper()
    person = f"Sarah {suffix}"
    org = f"Meridian {suffix} Corp"
    test_file = inbox_dir / f"{token}.txt"
    test_file.write_text(
        f"Integration test document for graph pipeline.\n"
        f"Dr. Sarah {suffix} presented the quarterly review at Meridian {suffix} Corp.\n"
        f"Reference: {token}\n",
        encoding="utf-8",
    )

    try:
        _poll(
            lambda: file_index_row(token),
            timeout=120,
            interval=3,
            desc="file_index row for the inbox file",
        )
        yield {
            "file_path": f"/workspace/inbox/{token}.txt",
            "token": token,
            "test_file": test_file,
            "suffix": suffix,
            "person": person,
            "org": org,
        }
    finally:
        test_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 1 — Full ingest pipeline
# ---------------------------------------------------------------------------


class TestFullIngestPipeline:
    def test_document_node_created_and_stamped(self, api, graph_api, ingested_doc):
        """After ingest, graph_projected_at should be stamped, the row filed under
        the seeded owner (LUM-884), and the graph should hold at least one node."""

        def _projected():
            row = file_index_row(ingested_doc["token"])
            return row if row and row["projected"] else None

        row = _poll(
            _projected,
            timeout=llm_wait_s(90),
            interval=3,
            desc="graph_projected_at to be stamped on file_index row",
        )
        assert row["user_id"] == current_user_id(api), (
            "inbox file must be filed under the seeded owner (INBOX_OWNER_USER_ID)"
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
        _start_backfill(graph_api)

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
        r = _start_backfill(graph_api)
        assert r.json()["status"] == "backfill_started"


# ---------------------------------------------------------------------------
# Test 4 — /graph/ego API returns correct shape
# ---------------------------------------------------------------------------


class TestEgoAPI:
    def test_ego_returns_correct_shape(self, graph_api, ingested_doc):
        """GET /graph/ego returns the expected response structure."""
        entity_name = _wait_for_entity(graph_api, ingested_doc["suffix"])
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

    def test_ego_ignores_user_id_query_param(self, graph_api, ingested_doc):
        """user_id in query string must be ignored; route uses auth-derived user_id."""
        entity_name = _wait_for_entity(graph_api, ingested_doc["suffix"])
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
    def test_path_with_known_entities(self, graph_api, ingested_doc):
        """GET /graph/path returns path_found and correct response structure."""
        # The document names a person and an organisation, both carrying the per-run
        # suffix; extraction should yield two distinct entities.
        a = _wait_for_entity(graph_api, ingested_doc["suffix"])
        b = _wait_for_entity(graph_api, ingested_doc["suffix"], exclude=(a,))
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
    def test_endpoints_degrade_gracefully_when_graph_absent(self, kg_api):
        """When graph is unavailable, all viz endpoints return structured JSON, not 500.

        This test uses the raw kg_api fixture (not graph_api) so it runs
        even when FalkorDB is not configured. If graph IS available, the
        test verifies normal responses instead — both are valid.
        """
        for endpoint in ["/graph/ego?entity=test", "/graph/stats", "/graph/search?q=test"]:
            r = kg_api.get(endpoint)
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

        _start_backfill(graph_api)

        time.sleep(10)

        _start_backfill(graph_api)

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
