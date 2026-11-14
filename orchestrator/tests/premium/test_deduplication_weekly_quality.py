# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Weekly KG quality job integration slices (premium — ``services.edge_quality``)."""

from __future__ import annotations

from unittest.mock import patch


class TestWeeklyJobIntegration:
    def test_dedup_runs_as_final_step(self):
        """Deduplication is called after edge scoring and constraint checks."""
        call_order = []

        def mock_edge_job(user_id="default"):
            call_order.append("edge")
            return {
                "pairs_computed": 1,
                "pairs_upserted": 1,
                "falkordb_updated": 0,
                "duration_ms": 10,
            }

        def mock_dedup_job(user_id="default"):
            call_order.append("dedup")
            return {
                "run_id": "r1",
                "candidate_count": 2,
                "auto_merged": 1,
                "queued_for_review": 1,
                "duration_ms": 50,
            }

        def mock_orphan_check(user_id="default"):
            call_order.append("orphan")
            return 0

        def mock_alias_check(user_id="default"):
            call_order.append("alias")
            return 0

        with (
            patch("services.edge_quality.run_edge_quality_job", side_effect=mock_edge_job),
            patch(
                "services.entity_constraints.check_orphan_entities", side_effect=mock_orphan_check
            ),
            patch(
                "services.entity_constraints.check_alias_uniqueness", side_effect=mock_alias_check
            ),
            patch("services.deduplication.run_deduplication_job", side_effect=mock_dedup_job),
        ):
            from services.edge_quality import run_weekly_quality_job

            run_weekly_quality_job()

        assert call_order.index("edge") < call_order.index("dedup")
        assert call_order.index("dedup") == len(call_order) - 1

    def test_dedup_exception_does_not_prevent_other_steps(self):
        """An exception in deduplication must not fail edge scoring or constraints."""
        edge_called = []
        orphan_called = []
        alias_called = []

        def mock_edge_job(user_id="default"):
            edge_called.append(True)
            return {
                "pairs_computed": 0,
                "pairs_upserted": 0,
                "falkordb_updated": 0,
                "duration_ms": 0,
            }

        def mock_orphan_check(user_id="default"):
            orphan_called.append(True)
            return 0

        def mock_alias_check(user_id="default"):
            alias_called.append(True)
            return 0

        def mock_dedup_job_raises(user_id="default"):
            raise RuntimeError("dedup exploded")

        with (
            patch("services.edge_quality.run_edge_quality_job", side_effect=mock_edge_job),
            patch(
                "services.entity_constraints.check_orphan_entities", side_effect=mock_orphan_check
            ),
            patch(
                "services.entity_constraints.check_alias_uniqueness", side_effect=mock_alias_check
            ),
            patch(
                "services.deduplication.run_deduplication_job", side_effect=mock_dedup_job_raises
            ),
        ):
            from services.edge_quality import run_weekly_quality_job

            run_weekly_quality_job()

        assert edge_called, "edge quality job must have run"
        assert orphan_called, "orphan check must have run"
        assert alias_called, "alias check must have run"

    def test_weekly_result_includes_dedup_fields(self):
        """Summary dict includes auto_merged and queued_for_review."""

        def mock_edge_job(user_id="default"):
            return {
                "pairs_computed": 5,
                "pairs_upserted": 5,
                "falkordb_updated": 0,
                "duration_ms": 100,
            }

        def mock_dedup_job(user_id="default"):
            return {
                "run_id": "r1",
                "candidate_count": 3,
                "auto_merged": 2,
                "queued_for_review": 1,
                "duration_ms": 200,
            }

        with (
            patch("services.edge_quality.run_edge_quality_job", side_effect=mock_edge_job),
            patch("services.entity_constraints.check_orphan_entities", return_value=0),
            patch("services.entity_constraints.check_alias_uniqueness", return_value=0),
            patch("services.deduplication.run_deduplication_job", side_effect=mock_dedup_job),
        ):
            from services.edge_quality import run_weekly_quality_job

            result = run_weekly_quality_job()

        assert "auto_merged" in result
        assert "queued_for_review" in result
        assert result["auto_merged"] == 2
        assert result["queued_for_review"] == 1

    def test_weekly_result_dedup_fields_zero_on_dedup_exception(self):
        """If dedup raises, auto_merged and queued_for_review default to 0."""

        def mock_edge_job(user_id="default"):
            return {
                "pairs_computed": 0,
                "pairs_upserted": 0,
                "falkordb_updated": 0,
                "duration_ms": 0,
            }

        with (
            patch("services.edge_quality.run_edge_quality_job", side_effect=mock_edge_job),
            patch("services.entity_constraints.check_orphan_entities", return_value=0),
            patch("services.entity_constraints.check_alias_uniqueness", return_value=0),
            patch("services.deduplication.run_deduplication_job", side_effect=RuntimeError("boom")),
        ):
            from services.edge_quality import run_weekly_quality_job

            result = run_weekly_quality_job()

        assert result["auto_merged"] == 0
        assert result["queued_for_review"] == 0
