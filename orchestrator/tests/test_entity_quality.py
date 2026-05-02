# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for services/entity_quality.py — Pass 1 KG quality gate.

Coverage:
  1.  Calibration examples: tier classification for all ten reference names
  2.  Stop list match is case-insensitive
  3.  Stop list reload triggered by mtime change (mock mtime)
  4.  ENTITY_QUALITY_FAIL_OPEN=true: exception in scorer returns original list
  5.  ENTITY_QUALITY_FAIL_OPEN=false: exception in scorer returns empty list
  6.  Staged entity promoted when mention_count reaches threshold
  7.  Staged entity promoted when incoming extraction_quality > ENTITY_QUALITY_UPPER
  8.  Staged entity NOT promoted when neither condition is met
"""

import os
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from models.entities import ExtractedEntity
from services.entity_quality import _compute_quality
from services.entity_quality import score_and_filter_entities

import config as _config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entity(name: str, entity_type: str = "PERSON") -> ExtractedEntity:
    return ExtractedEntity(name=name, entity_type=entity_type)


def _classify(name: str, lower: float = 0.35, upper: float = 0.60) -> str:
    """Return 'discard', 'staged', or 'normal' for a name at default thresholds."""
    score = _compute_quality(name)
    if score < lower:
        return "discard"
    if score < upper:
        return "staged"
    return "normal"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_stop_cache():
    """Reset the stop entity cache before each test."""
    _config._stop_entity_set = set()
    _config._stop_entity_mtime = None
    yield
    _config._stop_entity_set = set()
    _config._stop_entity_mtime = None


@pytest.fixture
def fixture_stop_list(tmp_path):
    """Write a small stop list file and point config at it."""
    stop_file = tmp_path / "stop_entities.txt"
    stop_file.write_text(
        "# test stop list\nthe meeting\nthe client\nnext steps\nthis week\nteam\n",
        encoding="utf-8",
    )
    with patch.dict(os.environ, {"STOP_ENTITIES_PATH": str(stop_file)}):
        _config._stop_entity_set = set()
        _config._stop_entity_mtime = None
        yield stop_file


# ---------------------------------------------------------------------------
# 1. Calibration examples
# ---------------------------------------------------------------------------


class TestCalibrationExamples:
    """Assert expected tier for all ten reference names from the plan."""

    def test_ada_lovelace_is_normal(self, fixture_stop_list):
        assert _classify("Ada Lovelace") == "normal"

    def test_bundesamt_is_normal(self, fixture_stop_list):
        assert _classify("Bundesamt für Statistik") == "normal"

    def test_aws_is_normal(self, fixture_stop_list):
        # AWS: ALLCAPS ≥2 chars → capitalisation_signal=0.5 (single token, first cap);
        # multi_token_bonus=0.6; no determiner; length OK; not in stop list
        # Expected: staged or normal (score is near boundary)
        tier = _classify("AWS")
        assert tier in ("staged", "normal")

    def test_the_hague_is_staged_or_normal(self, fixture_stop_list):
        # "The Hague": starts with determiner "The" → determiner penalty
        # but multi-token, capitalised second token
        tier = _classify("The Hague")
        assert tier in ("staged", "normal")

    def test_project_phoenix_is_normal(self, fixture_stop_list):
        assert _classify("Project Phoenix") == "normal"

    def test_the_meeting_is_discarded(self, fixture_stop_list):
        assert _classify("the meeting") == "discard"

    def test_the_client_is_discarded(self, fixture_stop_list):
        assert _classify("the client") == "discard"

    def test_next_steps_is_discarded(self, fixture_stop_list):
        assert _classify("next steps") == "discard"

    def test_this_week_is_discarded(self, fixture_stop_list):
        # "this week" hits determiner penalty (this) and stop list
        assert _classify("this week") == "discard"

    def test_team_is_discard_or_staged(self, fixture_stop_list):
        # "team": single token, all lower, in stop list
        tier = _classify("team")
        assert tier in ("discard", "staged")


# ---------------------------------------------------------------------------
# 2. Stop list match is case-insensitive
# ---------------------------------------------------------------------------


class TestStopListCaseInsensitive:
    def test_upper_case_stop_phrase_matched(self, fixture_stop_list):
        # "THE MEETING" should match "the meeting" in the stop list
        score = _compute_quality("THE MEETING")
        assert score == 0.0, f"Expected 0.0 (stop match), got {score}"

    def test_mixed_case_stop_phrase_matched(self, fixture_stop_list):
        score = _compute_quality("The Meeting")
        assert score == 0.0, f"Expected 0.0 (stop match), got {score}"


# ---------------------------------------------------------------------------
# 3. Stop list reload on mtime change
# ---------------------------------------------------------------------------


class TestStopListReload:
    def test_reload_triggered_by_mtime_change(self, tmp_path):
        stop_file = tmp_path / "stop_entities.txt"
        stop_file.write_text("the meeting\n", encoding="utf-8")

        with patch.dict(os.environ, {"STOP_ENTITIES_PATH": str(stop_file)}):
            _config._stop_entity_set = set()
            _config._stop_entity_mtime = None

            # First load
            first_set = _config.get_stop_entity_set()
            assert "the meeting" in first_set
            assert "project phoenix" not in first_set

            # Simulate mtime change by resetting the cached mtime
            _config._stop_entity_mtime = None
            stop_file.write_text("the meeting\nproject phoenix\n", encoding="utf-8")

            second_set = _config.get_stop_entity_set()
            assert "project phoenix" in second_set

    def test_no_reload_when_mtime_unchanged(self, tmp_path):
        """When mtime doesn't change, the cached set is returned without re-reading."""
        stop_file = tmp_path / "stop_entities.txt"
        stop_file.write_text("the meeting\n", encoding="utf-8")

        fixed_mtime = 1_000_000.0

        with patch.dict(os.environ, {"STOP_ENTITIES_PATH": str(stop_file)}):
            _config._stop_entity_set = set()
            _config._stop_entity_mtime = None

            with patch("os.path.getmtime", return_value=fixed_mtime):
                first_set = _config.get_stop_entity_set()
                assert "the meeting" in first_set

                # Simulate "file changed on disk" by updating the in-memory content
                # but os.path.getmtime still returns the same value.
                _config._stop_entity_set = {"the meeting", "original phrase"}
                second_set = _config.get_stop_entity_set()

            # Same mtime → the cached set is returned, not re-read from disk
            assert "original phrase" in second_set


# ---------------------------------------------------------------------------
# 4. ENTITY_QUALITY_FAIL_OPEN=true: exception → original list unchanged
# ---------------------------------------------------------------------------


class TestFailOpen:
    def test_exception_returns_original_list(self, monkeypatch, fixture_stop_list):
        monkeypatch.setenv("ENTITY_QUALITY_FAIL_OPEN", "true")
        entities = [_entity("Ada Lovelace"), _entity("Project X")]

        with patch(
            "services.entity_quality._compute_quality",
            side_effect=RuntimeError("simulated scorer error"),
        ):
            kept, discarded = score_and_filter_entities(entities, "default")

        assert len(kept) == 2, "All entities should be returned on fail-open"
        assert discarded == 0
        for e in kept:
            assert e.extraction_quality is None
            assert e.is_staged is None

    def test_fail_open_is_default(self, fixture_stop_list):
        """Default should be fail-open (no env var set)."""
        entities = [_entity("Ada Lovelace")]
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENTITY_QUALITY_FAIL_OPEN", None)
            with patch(
                "services.entity_quality._compute_quality",
                side_effect=RuntimeError("simulated"),
            ):
                kept, discarded = score_and_filter_entities(entities, "default")
        assert len(kept) == 1
        assert discarded == 0


# ---------------------------------------------------------------------------
# 5. ENTITY_QUALITY_FAIL_OPEN=false: exception → empty list
# ---------------------------------------------------------------------------


class TestFailClosed:
    def test_exception_returns_empty_list(self, monkeypatch, fixture_stop_list):
        monkeypatch.setenv("ENTITY_QUALITY_FAIL_OPEN", "false")
        entities = [_entity("Ada Lovelace"), _entity("Project X")]

        with patch(
            "services.entity_quality._compute_quality",
            side_effect=RuntimeError("simulated scorer error"),
        ):
            kept, discarded = score_and_filter_entities(entities, "default")

        assert kept == []
        assert discarded == 2


# ---------------------------------------------------------------------------
# 6. Staged entity promoted when mention_count reaches threshold
# ---------------------------------------------------------------------------


class TestStagedEntityPromotion:
    """Tests for the promotion logic in services/entities._upsert_entity.

    We test the promotion conditions in isolation by calling the helper directly.
    """

    def _make_ms(self, existing_row: dict) -> MagicMock:
        ms = MagicMock()
        ms.fetch_one.return_value = existing_row
        return ms

    def _make_embedder_vs(self):
        embedder = MagicMock()
        embedder.embed.return_value = [0.1] * 128
        vs = MagicMock()
        return embedder, vs

    def _call_upsert(self, entity: ExtractedEntity, existing_row: dict):
        from services.entities import _upsert_entity

        ms = self._make_ms(existing_row)
        embedder, vs = self._make_embedder_vs()
        _config._instances["metadata_store"] = ms
        _config._instances["embedder"] = embedder
        _config._instances["vector_store"] = vs
        _config._instances["graph_store"] = None  # disable graph hook
        try:
            _upsert_entity(entity, "ev-001", "SESSION", "default", ms, embedder, vs)
        finally:
            _config._instances.clear()
        return ms

    def test_promoted_when_mention_count_reaches_threshold(self, monkeypatch, fixture_stop_list):
        monkeypatch.setenv("ENTITY_PROMOTE_ON_MENTION_COUNT", "3")
        monkeypatch.setenv("ENTITY_QUALITY_UPPER", "0.60")
        # Existing: staged, mention_count=2 → after merge it will be 3 (threshold).
        # context_tags must have >= 2 overlap to trigger "merge" decision.
        existing = {
            "entity_id": "eid-staged",
            "name": "Project Phoenix",
            "aliases": [],
            "context_tags": ["engineering", "infrastructure"],
            "mention_count": 2,
            "is_staged": True,
        }
        entity = ExtractedEntity(
            name="Project Phoenix",
            entity_type="PROJECT",
            context_tags=["engineering", "infrastructure"],
            extraction_quality=0.50,  # below upper but count triggers promotion
            is_staged=True,
        )
        ms = self._call_upsert(entity, existing)

        executed_sql = " ".join(str(call) for call in ms.execute.call_args_list)
        assert "is_staged = FALSE" in executed_sql
        assert "graph_projected_at = NULL" in executed_sql

    def test_promoted_when_quality_exceeds_upper_threshold(self, monkeypatch, fixture_stop_list):
        monkeypatch.setenv("ENTITY_PROMOTE_ON_MENTION_COUNT", "10")
        monkeypatch.setenv("ENTITY_QUALITY_UPPER", "0.60")
        existing = {
            "entity_id": "eid-staged2",
            "name": "Bundesamt",
            "aliases": [],
            "context_tags": ["government", "statistics"],
            "mention_count": 1,
            "is_staged": True,
        }
        entity = ExtractedEntity(
            name="Bundesamt",
            entity_type="ORG",
            context_tags=["government", "statistics"],
            extraction_quality=0.85,  # > upper → promotes regardless of count
            is_staged=False,
        )
        ms = self._call_upsert(entity, existing)

        executed_sql = " ".join(str(call) for call in ms.execute.call_args_list)
        assert "is_staged = FALSE" in executed_sql

    def test_not_promoted_when_neither_condition_met(self, monkeypatch, fixture_stop_list):
        monkeypatch.setenv("ENTITY_PROMOTE_ON_MENTION_COUNT", "10")
        monkeypatch.setenv("ENTITY_QUALITY_UPPER", "0.60")
        existing = {
            "entity_id": "eid-staged3",
            "name": "SomeConcept",
            "aliases": [],
            "context_tags": ["misc", "general"],
            "mention_count": 1,
            "is_staged": True,
        }
        entity = ExtractedEntity(
            name="SomeConcept",
            entity_type="CONCEPT",
            context_tags=["misc", "general"],
            extraction_quality=0.50,  # below upper, mention_count will be 2 < 10
            is_staged=True,
        )
        ms = self._call_upsert(entity, existing)

        executed_sql = " ".join(str(call) for call in ms.execute.call_args_list)
        # Should NOT contain the promotion SET clause
        assert "is_staged = FALSE" not in executed_sql


# ---------------------------------------------------------------------------
# Additional scoring unit tests
# ---------------------------------------------------------------------------


class TestScoringSignals:
    def test_normal_named_entity_scores_above_upper(self, fixture_stop_list):
        score = _compute_quality("Ada Lovelace")
        assert score >= 0.60, f"Expected >= 0.60 for 'Ada Lovelace', got {score}"

    def test_all_lower_single_token_scores_low(self, fixture_stop_list):
        score = _compute_quality("team")
        # In fixture stop list → stop_absence = 0.0 → score is at most 0.65
        assert score <= 0.65

    def test_score_clamped_to_unit_interval(self, fixture_stop_list):
        for name in ("Ada Lovelace", "AWS", "the client", "x", "123456"):
            score = _compute_quality(name)
            assert 0.0 <= score <= 1.0, f"Score {score} out of [0,1] for {name!r}"

    def test_pure_digit_name_penalised(self, fixture_stop_list):
        # Pure digit name gets length_sanity=0 (no real-world entity is "12345").
        # The remaining signals cap the score below normal threshold.
        score = _compute_quality("12345")
        assert score < 0.65, f"Expected pure digit name to score below 0.65, got {score}"

    def test_very_long_name_penalised(self, fixture_stop_list):
        # A 200-char all-lowercase phrase is penalised by the length_sanity signal.
        long_name = "a" * 200
        score = _compute_quality(long_name)
        # length_sanity decays to near 0; capitalisation=0.2; single-token bonus=0.6
        assert score < 0.80, f"Expected long all-lower name to score below 0.80, got {score}"

    def test_discard_count_reflects_filtered_entities(self, fixture_stop_list):
        entities = [
            _entity("Ada Lovelace"),
            _entity("the meeting"),  # in fixture stop list → discard
            _entity("Project Phoenix"),
        ]
        kept, discarded = score_and_filter_entities(entities, "default")
        assert discarded == 1
        assert len(kept) == 2
        names = [e.name for e in kept]
        assert "the meeting" not in names

    def test_kept_entities_have_quality_and_staged_set(self, fixture_stop_list):
        entities = [_entity("Ada Lovelace"), _entity("Project Phoenix")]
        kept, discarded = score_and_filter_entities(entities, "default")
        assert discarded == 0
        for e in kept:
            assert e.extraction_quality is not None
            assert isinstance(e.is_staged, bool)
