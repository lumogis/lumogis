# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Phase 3 machine-verifiable validation checkpoint (M9).

This module provides automated gates that confirm Phase 3 correctness.
When all gates pass, the following statement is verified:

  "Ingest a document, entities project into FalkorDB, reconciliation heals
  gaps, the viz API returns correct subgraphs, and the self-healing model
  works end to end."

Gate structure:
  Gate 1 — Reconciliation completeness (automated)
  Gate 2 — All integration tests pass (satisfied by running the full suite)
  Gate 3 — Subjective product checkpoint (manual sign-off, documented here)

Run:
  make compose-test-integration
"""

import json

import pytest

pytestmark = [pytest.mark.integration]


class TestPhase3ValidationCheckpoint:
    """Machine-verifiable validation checkpoint for Phase 3 close-out."""

    # -------------------------------------------------------------------
    # Gate 1 — Reconciliation completeness
    # -------------------------------------------------------------------

    def test_gate1_no_stale_rows_across_tables(self, api):
        """Verify all projected tables have graph_projected_at stamped.

        Queries the /export endpoint and checks each table for rows where
        graph_projected_at is NULL. An empty table is not a failure — only
        rows with missing stamps indicate reconciliation gaps.

        This gate requires FalkorDB to be configured and a backfill to have
        run at least once. If graph is unavailable, the gate is skipped
        (not failed) — a system without FalkorDB has no graph projection
        requirements.
        """
        stats = api.get("/graph/stats")
        if stats.status_code != 200 or not stats.json().get("available", False):
            pytest.skip(
                "FalkorDB not available — Gate 1 (reconciliation completeness) "
                "requires a configured graph backend"
            )

        r = api.get("/export")
        assert r.status_code == 200, f"/export returned {r.status_code}"

        tables_with_projection = {
            "entities": "graph_projected_at",
            "file_index": "graph_projected_at",
            "sessions": "graph_projected_at",
            "notes": "graph_projected_at",
            # audio_memos checked separately if present
        }

        stale_report: dict[str, int] = {}

        for line in r.text.strip().split("\n"):
            if not line.strip():
                continue
            section = json.loads(line)
            table = section.get("section", "")
            rows = section.get("rows", [])

            if table not in tables_with_projection:
                continue

            stamp_col = tables_with_projection[table]

            # Empty table: not a failure
            if not rows:
                continue

            unstamped = sum(1 for row in rows if not row.get(stamp_col))
            if unstamped > 0:
                stale_report[table] = unstamped

        if stale_report:
            detail = ", ".join(f"{t}: {c} unstamped" for t, c in stale_report.items())
            pytest.fail(
                f"Gate 1 FAILED: stale rows found — {detail}. "
                f"Run POST /graph/backfill and re-check."
            )

    # -------------------------------------------------------------------
    # Gate 2 — All integration tests pass
    # -------------------------------------------------------------------
    #
    # Satisfied by running the full integration suite:
    #   make compose-test-integration
    #
    # This gate does not require additional code — it is a prerequisite
    # documented here for the validation checkpoint record.

    # -------------------------------------------------------------------
    # Gate 3 — Subjective product checkpoint (manual sign-off)
    # -------------------------------------------------------------------
    #
    # The following three criteria require human evaluation:
    #
    #   1. Does "What do you know about Sarah Chen?" return a meaningful
    #      dossier with graph context?
    #      → Open LibreChat, ask the question, verify the response includes
    #        graph-sourced relationship context (e.g. "[Graph] Sarah Chen
    #        relates to: ...").
    #
    #   2. Does the visualization page load and show a navigable graph?
    #      → Open http://localhost:8000/graph/viz in a browser, search for
    #        an entity, verify the Cytoscape.js canvas renders nodes and
    #        edges, and that clicking a node shows its details.
    #
    #   3. Does the self-healing smoke test complete successfully?
    #      → Run: bash scripts/smoke_test_m2.sh
    #        This stops FalkorDB, ingests a document, restarts FalkorDB,
    #        triggers backfill, and verifies the document is projected.

    @pytest.mark.manual
    def test_gate3_dossier_query_with_graph_context(self):
        """Manual: ask 'What do you know about Sarah Chen?' and verify
        graph context appears in the response."""
        pytest.skip(
            "Manual sign-off: verify graph context in dossier query "
            "(see test docstring for procedure)"
        )

    @pytest.mark.manual
    def test_gate3_viz_page_navigable(self):
        """Manual: open /graph/viz and verify Cytoscape.js renders correctly."""
        pytest.skip(
            "Manual sign-off: verify visualization page loads and shows "
            "a navigable graph (see test docstring for procedure)"
        )

    @pytest.mark.manual
    def test_gate3_self_healing_smoke_test(self):
        """Manual: run scripts/smoke_test_m2.sh end to end."""
        pytest.skip(
            "Manual sign-off: run scripts/smoke_test_m2.sh and verify "
            "the self-healing pipeline completes (see test docstring)"
        )
