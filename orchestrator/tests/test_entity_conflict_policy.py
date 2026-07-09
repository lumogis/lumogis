# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for entity field-tier conflict policy (LUM-358)."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

from services.entity_conflict_policy import ConflictTier
from services.entity_conflict_policy import format_represent_both_summary
from services.entity_conflict_policy import merge_metadata_field
from services.entity_conflict_policy import requires_review
from services.entity_conflict_policy import tier_for_field


def test_tier_for_field_summary():
    assert tier_for_field("summary") == ConflictTier.SUMMARY


def test_tier_for_field_last_verified():
    assert tier_for_field("last_verified_at") == ConflictTier.METADATA


def test_tier_for_field_unknown_defaults_to_summary():
    assert tier_for_field("mystery_field") == ConflictTier.SUMMARY


def test_merge_context_tags_union():
    result = merge_metadata_field("context_tags", ["a"], ["b"])
    assert result == ["a", "b"]


def test_merge_aliases_union():
    result = merge_metadata_field("aliases", ["x"], ["y", "x"])
    assert result == ["x", "y"]


def test_merge_last_verified_at_newest_wins():
    older = datetime(2026, 1, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert merge_metadata_field("last_verified_at", older, newer) == newer
    assert merge_metadata_field("last_verified_at", newer, older) == newer


def test_format_represent_both_summary_sorted():
    text = format_represent_both_summary([("bob", "B text"), ("alice", "A text")])
    assert text == "alice: A text · bob: B text"


def test_requires_review_categories():
    assert requires_review("logistics") is True
    assert requires_review("focus") is True
    assert requires_review("relationship") is True
    assert requires_review("preference") is False
