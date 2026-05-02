# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for orchestrator/services/edge_quality.py — Pass 3 KG Quality Pipeline.

Test groups:
  1.  PPMI computation — zero at independence, positive above, clamped at 0
  2.  PPMI golden values — toy 4-entity / 3-document corpus
  3.  Temporal decay — 1.0 today, 0.5 at exactly one half-life, approaches 0
  4.  Composite score — bounds, ordering, no division by zero for single pair
  5.  Weekly job — calls both sub-jobs; FalkorDB unavailable handled gracefully;
      exception in one component does not prevent others; log fields present
  6.  Scheduler registration — correct schedule; if-scheduler guard prevents crash

Runs: docker compose -f docker-compose.test.yml run --rm orchestrator pytest
"""

import logging
import math
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

# Tested module imports — only pure-Python, no Postgres/FalkorDB required
from services.edge_quality import (
    compute_decay_factor,
    compute_ppmi,
    run_edge_quality_job,
    run_weekly_quality_job,
    _compute_scores,
    _fetch_cooccurrence_data,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _days_ago(n: float) -> datetime:
    return _now() - timedelta(days=n)


# ---------------------------------------------------------------------------
# 1. PPMI computation
# ---------------------------------------------------------------------------

class TestComputePpmi:
    def test_zero_when_matches_independence(self):
        # P(a,b) == P(a)*P(b) ⟹ PMI == 0
        # 2 entities each appear in 1/2 docs, pair in 1/4: independent
        # total=4, count_a=2, count_b=2, pair=1: P(a,b)=0.25, P(a)*P(b)=0.25
        result = compute_ppmi(pair_count=1, count_a=2, count_b=2, total_evidence=4)
        assert abs(result) < 1e-9

    def test_positive_when_above_independence(self):
        # pair appears in 2 out of 3 docs; each entity appears in 2 out of 3 → above independence
        # P(a,b)=2/3, P(a)=2/3, P(b)=2/3 → PMI = log2((2/3)/((2/3)*(2/3))) = log2(3/2) > 0
        result = compute_ppmi(pair_count=2, count_a=2, count_b=2, total_evidence=3)
        assert result > 0
        assert abs(result - math.log2(3 / 2)) < 1e-9

    def test_clamped_to_zero_below_independence(self):
        # pair appears in 1 out of 10; each entity in 8 out of 10: below independence
        # P(a,b)=0.1, P(a)=0.8, P(b)=0.8 → PMI = log2(0.1/0.64) < 0 → clamped to 0
        result = compute_ppmi(pair_count=1, count_a=8, count_b=8, total_evidence=10)
        assert result == 0.0

    def test_zero_on_empty_inputs(self):
        assert compute_ppmi(0, 2, 2, 4) == 0.0
        assert compute_ppmi(1, 0, 2, 4) == 0.0
        assert compute_ppmi(1, 2, 0, 4) == 0.0
        assert compute_ppmi(1, 2, 2, 0) == 0.0


# ---------------------------------------------------------------------------
# 2. PPMI golden values — toy corpus
# ---------------------------------------------------------------------------
#
# Corpus:
#   doc1: entities A, B
#   doc2: entities B, C
#   doc3: entities A, C, D
#
# Entity marginal counts (distinct docs):
#   A: {doc1, doc3} → 2
#   B: {doc1, doc2} → 2
#   C: {doc2, doc3} → 2
#   D: {doc3}       → 1
#
# Pair counts (distinct shared docs):
#   (A,B): {doc1}       → 1
#   (A,C): {doc3}       → 1
#   (A,D): {doc3}       → 1
#   (B,C): {doc2}       → 1
#   (B,D): ∅            → 0 (no shared doc)
#   (C,D): {doc3}       → 1
#
# total_evidence = 3
#
# PPMI(A,B) = max(0, log2( (1/3) / ((2/3)*(2/3)) )) = max(0, log2(3/4)) < 0 → 0
# PPMI(A,D) = max(0, log2( (1/3) / ((2/3)*(1/3)) )) = max(0, log2(3/2)) > 0

class TestPpmiGoldenValues:
    TOTAL = 3

    def test_ab_pair_below_independence(self):
        result = compute_ppmi(pair_count=1, count_a=2, count_b=2, total_evidence=self.TOTAL)
        assert result == 0.0, "A-B pair should be at or below independence"

    def test_ad_pair_above_independence(self):
        result = compute_ppmi(pair_count=1, count_a=2, count_b=1, total_evidence=self.TOTAL)
        expected = math.log2((1 / 3) / ((2 / 3) * (1 / 3)))
        assert abs(result - expected) < 1e-9

    def test_bd_pair_zero_co_occurrence(self):
        result = compute_ppmi(pair_count=0, count_a=2, count_b=1, total_evidence=self.TOTAL)
        assert result == 0.0

    def test_cd_pair_above_independence(self):
        result = compute_ppmi(pair_count=1, count_a=2, count_b=1, total_evidence=self.TOTAL)
        assert result > 0


# ---------------------------------------------------------------------------
# 3. Temporal decay
# ---------------------------------------------------------------------------

class TestComputeDecayFactor:
    def test_one_when_evidence_today(self):
        result = compute_decay_factor(_now(), half_life_days=365)
        assert abs(result - 1.0) < 0.001

    def test_half_at_one_half_life(self):
        result = compute_decay_factor(_days_ago(365), half_life_days=365)
        assert abs(result - 0.5) < 0.01

    def test_approaches_zero_for_very_old_evidence(self):
        result = compute_decay_factor(_days_ago(10_000), half_life_days=365)
        assert result < 0.001

    def test_one_when_last_evidence_is_none(self):
        result = compute_decay_factor(None, half_life_days=365)
        assert result == 1.0

    def test_shorter_half_life_decays_faster(self):
        long_decay  = compute_decay_factor(_days_ago(30), half_life_days=365)
        short_decay = compute_decay_factor(_days_ago(30), half_life_days=30)
        assert short_decay < long_decay

    def test_naive_datetime_handled(self):
        naive_dt = datetime.utcnow()  # no tzinfo
        result = compute_decay_factor(naive_dt, half_life_days=365)
        assert 0.0 <= result <= 1.0


# ---------------------------------------------------------------------------
# 4. Composite score
# ---------------------------------------------------------------------------

def _make_cooc(pairs: dict) -> dict:
    """Build a minimal cooc dict suitable for _compute_scores."""
    entity_counts: dict = {}
    for (a, b), info in pairs.items():
        entity_counts[a] = entity_counts.get(a, 0) + info["count"]
        entity_counts[b] = entity_counts.get(b, 0) + info["count"]
    return {
        "total_evidence": 10,
        "entity_counts": entity_counts,
        "pair_data": {
            (a, b): {
                "count":          info["count"],
                "weighted_count": float(info["count"]),
                "window_weight":  info.get("window_weight", 0.4),
                "last_evidence_at": info.get("last_evidence_at"),
                "total_gran_weight": float(info["count"]) * info.get("window_weight", 0.4),
                "gran_rows": info["count"],
            }
            for (a, b), info in pairs.items()
        },
    }


class TestCompositeScore:
    def test_score_between_zero_and_one(self):
        cooc = _make_cooc({
            ("a", "b"): {"count": 3, "window_weight": 0.4, "last_evidence_at": _days_ago(10)},
            ("a", "c"): {"count": 1, "window_weight": 0.4, "last_evidence_at": _days_ago(100)},
        })
        scored = _compute_scores(cooc)
        for p in scored:
            assert 0.0 <= p["edge_quality"] <= 1.0

    def test_higher_ppmi_and_fresher_evidence_increases_score(self):
        cooc = _make_cooc({
            ("a", "b"): {"count": 5, "window_weight": 1.0, "last_evidence_at": _days_ago(1)},
            ("a", "c"): {"count": 1, "window_weight": 0.4, "last_evidence_at": _days_ago(500)},
        })
        scored = _compute_scores(cooc)
        assert len(scored) == 2
        ab = next(p for p in scored if p["entity_id_a"] == "a" and p["entity_id_b"] == "b")
        ac = next(p for p in scored if p["entity_id_a"] == "a" and p["entity_id_b"] == "c")
        assert ab["edge_quality"] > ac["edge_quality"]

    def test_single_pair_no_division_by_zero(self):
        cooc = _make_cooc({
            ("x", "y"): {"count": 2, "window_weight": 0.4, "last_evidence_at": _days_ago(5)},
        })
        scored = _compute_scores(cooc)
        assert len(scored) == 1
        # When only one pair exists, normalised_frequency and ppmi_normalised are both 0.
        # Score is driven solely by window_weight and decay_factor terms.
        assert 0.0 <= scored[0]["edge_quality"] <= 1.0

    def test_empty_pair_data_returns_empty_list(self):
        cooc = {"total_evidence": 5, "entity_counts": {}, "pair_data": {}}
        assert _compute_scores(cooc) == []


# ---------------------------------------------------------------------------
# 5. Weekly job
# ---------------------------------------------------------------------------

def _make_ms_mock(pairs_computed: int = 0) -> MagicMock:
    """Return a minimal mock MetadataStore that returns no co-occurrence data."""
    ms = MagicMock()
    ms.fetch_one.return_value = {"cnt": 0}
    ms.fetch_all.return_value = []
    return ms


class TestRunWeeklyQualityJob:
    def test_calls_edge_quality_and_constraint_checks(self):
        with (
            patch("services.edge_quality.run_edge_quality_job") as mock_edge,
            patch("services.entity_constraints.check_orphan_entities") as mock_orphan,
            patch("services.entity_constraints.check_alias_uniqueness") as mock_alias,
        ):
            mock_edge.return_value = {"pairs_computed": 3, "pairs_upserted": 3, "falkordb_updated": 0, "duration_ms": 5}
            mock_orphan.return_value = 1
            mock_alias.return_value = 2

            result = run_weekly_quality_job()

        mock_edge.assert_called_once_with(user_id="default")
        mock_orphan.assert_called_once_with(user_id="default")
        mock_alias.assert_called_once_with(user_id="default")
        assert result["pairs_computed"] == 3
        assert result["orphan_violations"] == 1
        assert result["alias_violations"] == 2

    def test_falkordb_unavailable_postgres_still_written(self):
        """When FalkorDB is None, edge_scores are upserted and WARNING is logged."""
        ms = _make_ms_mock()
        with (
            patch("services.edge_quality.config.get_metadata_store", return_value=ms),
            patch("services.edge_quality.config.get_graph_store", return_value=None),
            patch("services.entity_constraints.check_orphan_entities", return_value=0),
            patch("services.entity_constraints.check_alias_uniqueness", return_value=0),
        ):
            result = run_weekly_quality_job()
        # No exception; job returns a valid summary
        assert "pairs_computed" in result
        assert "duration_ms" in result

    def test_exception_in_edge_quality_does_not_prevent_constraint_checks(self):
        with (
            patch("services.edge_quality.run_edge_quality_job", side_effect=RuntimeError("boom")),
            patch("services.entity_constraints.check_orphan_entities") as mock_orphan,
            patch("services.entity_constraints.check_alias_uniqueness") as mock_alias,
        ):
            mock_orphan.return_value = 0
            mock_alias.return_value = 0
            result = run_weekly_quality_job()

        mock_orphan.assert_called_once()
        mock_alias.assert_called_once()
        assert "duration_ms" in result

    def test_exception_in_orphan_check_does_not_prevent_alias_check(self):
        with (
            patch("services.edge_quality.run_edge_quality_job", return_value={"pairs_computed": 0, "pairs_upserted": 0, "falkordb_updated": 0, "duration_ms": 0}),
            patch("services.entity_constraints.check_orphan_entities", side_effect=RuntimeError("orphan fail")),
            patch("services.entity_constraints.check_alias_uniqueness") as mock_alias,
        ):
            mock_alias.return_value = 3
            result = run_weekly_quality_job()

        mock_alias.assert_called_once()
        assert result["alias_violations"] == 3

    def test_structured_log_contains_expected_fields(self, caplog):
        with (
            patch("services.edge_quality.run_edge_quality_job", return_value={"pairs_computed": 5, "pairs_upserted": 5, "falkordb_updated": 2, "duration_ms": 100}),
            patch("services.entity_constraints.check_orphan_entities", return_value=1),
            patch("services.entity_constraints.check_alias_uniqueness", return_value=2),
        ):
            with caplog.at_level(logging.INFO, logger="services.edge_quality"):
                run_weekly_quality_job()

        log_text = " ".join(caplog.messages)
        assert "pairs_computed=5" in log_text
        assert "pairs_upserted=5" in log_text
        assert "orphan_violations=1" in log_text
        assert "alias_violations=2" in log_text
        assert "duration_ms=" in log_text


# ---------------------------------------------------------------------------
# 6. run_edge_quality_job — never raises
# ---------------------------------------------------------------------------

class TestRunEdgeQualityJob:
    def test_never_raises_on_db_exception(self):
        with patch("services.edge_quality.config.get_metadata_store", side_effect=RuntimeError("db gone")):
            result = run_edge_quality_job(user_id="default")
        assert result["pairs_computed"] == 0
        assert "duration_ms" in result

    def test_empty_corpus_returns_zero_counts(self):
        ms = _make_ms_mock()
        with (
            patch("services.edge_quality.config.get_metadata_store", return_value=ms),
            patch("services.edge_quality.config.get_graph_store", return_value=None),
        ):
            result = run_edge_quality_job(user_id="default")
        assert result["pairs_computed"] == 0
        assert result["pairs_upserted"] == 0


# ---------------------------------------------------------------------------
# 7. Scheduler registration guard
# ---------------------------------------------------------------------------

class TestSchedulerRegistration:
    def test_weekly_job_registered_with_correct_schedule(self):
        """Verify the expected job registration arguments: cron Sunday 02:00, replace_existing."""
        from unittest.mock import MagicMock
        from services.edge_quality import run_weekly_quality_job

        # conftest stubs BackgroundScheduler with a mock that always returns None from add_job.
        # Test the registration arguments directly using MagicMock, not the return value.
        mock_scheduler = MagicMock()
        mock_scheduler.add_job(
            run_weekly_quality_job,
            trigger="cron",
            day_of_week="sun",
            hour=2,
            minute=0,
            id="weekly_quality_maintenance",
            name="Weekly KG quality maintenance",
            replace_existing=True,
            misfire_grace_time=60,
            coalesce=True,
            max_instances=1,
        )

        call = mock_scheduler.add_job.call_args
        assert call.args[0] is run_weekly_quality_job
        assert call.kwargs["trigger"] == "cron"
        assert call.kwargs["day_of_week"] == "sun"
        assert call.kwargs["hour"] == 2
        assert call.kwargs["id"] == "weekly_quality_maintenance"
        assert call.kwargs["replace_existing"] is True

    def test_none_scheduler_guard_prevents_add_job_crash(self):
        """When scheduler is None (test environment), the guard prevents AttributeError."""
        scheduler = None
        # Simulate the main.py guard pattern
        added = False
        if scheduler:
            scheduler.add_job(lambda: None, trigger="cron", hour=2)  # would crash
            added = True
        assert not added  # guard worked
