# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for services/signal_processor.py.

Tests cover the pure logic (match_relevance, score clamping) and the
process_signal pipeline with mocked LLM, notifier, and persistence.
No Docker or network required.
"""

import uuid
from datetime import datetime
from datetime import timezone
from unittest.mock import MagicMock

import pytest
from models.signals import RelevanceProfile
from models.signals import Signal
from services.signal_processor import match_relevance
from services.signal_processor import process_signal
from services.signal_processor import score_importance

import config as _config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_signal(**kwargs) -> Signal:
    defaults = dict(
        signal_id=str(uuid.uuid4()),
        source_id="src-1",
        title="Test Signal",
        url="https://example.com/test",
        published_at=datetime.now(timezone.utc),
        content_summary="A short summary.",
        raw_content="Full raw content for processing.",
        entities=[],
        topics=[],
        importance_score=0.5,
        relevance_score=0.0,
        notified=False,
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    return Signal(**defaults)


def _make_profile(**kwargs) -> RelevanceProfile:
    defaults = dict(
        id="profile-1",
        tracked_topics=["finance", "tech"],
        tracked_locations=["sydney"],
        tracked_entities=["reserve bank"],
        tracked_keywords=["interest rate"],
    )
    defaults.update(kwargs)
    return RelevanceProfile(**defaults)


def _mock_llm_response(data: dict):
    """Return a mock LLM provider whose .chat() yields a JSON-serialisable response."""
    import json

    mock_response = MagicMock()
    mock_response.text = json.dumps(data)
    mock_provider = MagicMock()
    mock_provider.chat.return_value = mock_response
    return mock_provider


# ---------------------------------------------------------------------------
# match_relevance — pure function, no mocks needed
# ---------------------------------------------------------------------------


class TestMatchRelevance:
    def test_full_topic_match(self):
        signal = _make_signal(topics=["finance", "tech"], importance_score=0.5)
        profile = _make_profile(
            tracked_topics=["finance", "tech"],
            tracked_locations=[],
            tracked_entities=[],
            tracked_keywords=[],
        )
        score = match_relevance(signal, profile)
        # topic weight 0.30 × 1.0 + importance 0.10 × 0.5
        assert score == pytest.approx(0.35, abs=0.01)

    def test_no_overlap_returns_only_importance(self):
        signal = _make_signal(
            topics=["sports"],
            entities=[],
            content_summary="Cricket news today",
            title="Cricket",
            importance_score=0.6,
        )
        profile = _make_profile(
            tracked_topics=["finance"],
            tracked_locations=["berlin"],
            tracked_entities=["ecb"],
            tracked_keywords=["interest rate"],
        )
        score = match_relevance(signal, profile)
        # Only importance contributes: 0.10 × 0.6 = 0.06
        assert score == pytest.approx(0.06, abs=0.01)

    def test_keyword_match_in_title(self):
        signal = _make_signal(
            title="Interest rate decision today",
            content_summary="RBA holds rates.",
            topics=[],
            entities=[],
            importance_score=0.0,
        )
        profile = _make_profile(
            tracked_topics=[],
            tracked_locations=[],
            tracked_entities=[],
            tracked_keywords=["interest rate"],
        )
        score = match_relevance(signal, profile)
        # keyword weight 0.20 × 1.0 = 0.20
        assert score == pytest.approx(0.20, abs=0.01)

    def test_score_clamped_to_one(self):
        signal = _make_signal(
            topics=["finance", "tech"],
            entities=[{"name": "Reserve Bank", "type": "ORG"}],
            content_summary="Interest rate sydney finance tech",
            title="Interest rate sydney",
            importance_score=1.0,
        )
        profile = _make_profile(
            tracked_topics=["finance", "tech"],
            tracked_locations=["sydney"],
            tracked_entities=["reserve bank"],
            tracked_keywords=["interest rate"],
        )
        score = match_relevance(signal, profile)
        assert score <= 1.0

    def test_empty_profile_returns_only_importance(self):
        signal = _make_signal(importance_score=0.8)
        profile = _make_profile(
            tracked_topics=[],
            tracked_locations=[],
            tracked_entities=[],
            tracked_keywords=[],
        )
        score = match_relevance(signal, profile)
        assert score == pytest.approx(0.08, abs=0.01)


# ---------------------------------------------------------------------------
# score_importance — cache behaviour
# ---------------------------------------------------------------------------


class TestScoreImportance:
    def test_returns_cached_value(self, monkeypatch):
        from services import signal_processor

        signal = _make_signal(url="https://example.com/cached")
        import hashlib

        key = hashlib.md5(signal.url.encode()).hexdigest()
        signal_processor._score_cache[key] = 0.77

        score = score_importance(signal)
        assert score == 0.77

    def test_clamps_llm_output_above_one(self, monkeypatch):
        llm = _mock_llm_response({"importance_score": 1.5})
        monkeypatch.setattr(_config, "get_llm_provider", lambda name: llm)
        monkeypatch.setattr("services.signal_processor.get_budget", lambda name: 4096)

        signal = _make_signal(url="https://example.com/clamp-high")
        score = score_importance(signal)
        assert score == 1.0

    def test_clamps_llm_output_below_zero(self, monkeypatch):
        llm = _mock_llm_response({"importance_score": -0.3})
        monkeypatch.setattr(_config, "get_llm_provider", lambda name: llm)
        monkeypatch.setattr("services.signal_processor.get_budget", lambda name: 4096)

        signal = _make_signal(url="https://example.com/clamp-low")
        score = score_importance(signal)
        assert score == 0.0


# ---------------------------------------------------------------------------
# process_signal — pipeline with mocked LLM and persistence
# ---------------------------------------------------------------------------


class TestProcessSignal:
    def _setup(self, monkeypatch, llm_data: dict, relevance_threshold: str = "0.0"):
        monkeypatch.setenv("SIGNAL_RELEVANCE_THRESHOLD", relevance_threshold)
        monkeypatch.setattr("services.signal_processor.get_budget", lambda name: 4096)

        llm = _mock_llm_response(llm_data)
        monkeypatch.setattr(_config, "get_llm_provider", lambda name: llm)

        mock_notifier = MagicMock()
        mock_notifier.notify.return_value = True
        monkeypatch.setattr(_config, "get_notifier", lambda: mock_notifier)

        monkeypatch.setattr("services.signal_processor._load_profile", lambda uid: None)
        monkeypatch.setattr("services.signal_processor._persist", lambda sig: None)
        return mock_notifier

    def test_returns_signal_with_llm_fields(self, monkeypatch):
        llm_data = {
            "content_summary": "RBA holds cash rate.",
            "topics": ["finance"],
            "entities": [{"name": "RBA", "type": "ORG"}],
            "importance_score": 0.8,
        }
        self._setup(monkeypatch, llm_data)
        raw = _make_signal(title="RBA Decision", url="https://rba.gov.au/news")
        result = process_signal(raw)

        assert result.content_summary == "RBA holds cash rate."
        assert result.topics == ["finance"]
        assert result.importance_score == pytest.approx(0.8)
        assert result.raw_content == ""  # cleared after processing

    def test_notifier_called_when_above_threshold(self, monkeypatch):
        llm_data = {
            "importance_score": 0.9,
            "content_summary": "Big news.",
            "topics": [],
            "entities": [],
        }
        notifier = self._setup(monkeypatch, llm_data, relevance_threshold="0.0")
        raw = _make_signal()
        result = process_signal(raw)
        assert notifier.notify.called
        assert result.notified is True

    def test_notifier_not_called_below_threshold(self, monkeypatch):
        llm_data = {
            "importance_score": 0.1,
            "content_summary": "Minor note.",
            "topics": [],
            "entities": [],
        }
        notifier = self._setup(monkeypatch, llm_data, relevance_threshold="0.9")
        raw = _make_signal()
        result = process_signal(raw)
        assert not notifier.notify.called
        assert result.notified is False

    def test_relevance_score_computed_when_profile_present(self, monkeypatch):
        llm_data = {
            "importance_score": 0.5,
            "content_summary": "Finance news",
            "topics": ["finance"],
            "entities": [],
        }
        self._setup(monkeypatch, llm_data, relevance_threshold="0.0")
        profile = _make_profile(
            tracked_topics=["finance"],
            tracked_locations=[],
            tracked_entities=[],
            tracked_keywords=[],
        )
        monkeypatch.setattr("services.signal_processor._load_profile", lambda uid: profile)
        raw = _make_signal()
        result = process_signal(raw)
        assert result.relevance_score > 0.0
