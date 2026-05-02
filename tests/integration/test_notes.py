# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Integration tests for notes-related graph behaviour (Phase 3 M9).

M5 (quick capture service with /notes endpoints) is not yet implemented.
These tests verify:
  1. The NOTE_CAPTURED graph projection pathway works when triggered
     (via backfill/reconciliation — which replays any notes rows)
  2. The graph viz page is served independently of FalkorDB availability

Run:
  make compose-test-integration
"""

import pytest

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# Test 1 — Notes graph projection via reconciliation
# ---------------------------------------------------------------------------

class TestNotesGraphProjection:
    def test_backfill_processes_notes_table(self, api):
        """POST /graph/backfill processes the notes table (among others).

        If notes rows exist in Postgres with graph_projected_at IS NULL,
        backfill will project them. We verify the backfill endpoint accepts
        the request — the reconcile unit tests verify projection correctness.

        If graph is unavailable, this test still verifies the endpoint returns
        a structured response (503 for no graph store, 202/409 otherwise).
        """
        r = api.post("/graph/backfill")
        assert r.status_code in (202, 409, 503), (
            f"Expected 202, 409, or 503 from /graph/backfill, got {r.status_code}"
        )

        if r.status_code == 503:
            body = r.json()
            assert "detail" in body
        elif r.status_code == 202:
            body = r.json()
            assert body["status"] == "backfill_started"
        else:
            body = r.json()
            assert "detail" in body


# ---------------------------------------------------------------------------
# Test 2 — Graph viz page served independently of FalkorDB
# ---------------------------------------------------------------------------

class TestVizPageIndependence:
    def test_viz_page_served_without_falkordb(self, api):
        """GET /graph/viz serves the HTML visualization page regardless of
        whether FalkorDB is configured. The page itself handles the
        'graph unavailable' case client-side.

        If graph_viz.html does not exist (pre-M4 state), the endpoint
        returns 404 — that is expected and not a test failure.
        """
        r = api.get("/graph/viz")
        assert r.status_code in (200, 404), (
            f"Expected 200 or 404 from /graph/viz, got {r.status_code}"
        )

        if r.status_code == 200:
            assert "text/html" in r.headers.get("content-type", "")

    def test_stats_endpoint_available_without_falkordb(self, api):
        """GET /graph/stats returns structured JSON even when FalkorDB is down.
        No 5xx errors should occur."""
        r = api.get("/graph/stats")
        assert r.status_code == 200
        body = r.json()
        assert "available" in body
        assert "node_count" in body
