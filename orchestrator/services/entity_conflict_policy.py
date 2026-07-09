# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Entity field-tier conflict policy — pure functions (LUM-358).

Mirrors LUM-514 represent-both vocabulary for entity-summary divergence.
No I/O. See ``.cursor/plans/LUM-358-household-concurrent-write-isolation.plan.md``.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

_REVIEW_CATEGORIES = frozenset({"logistics", "focus", "relationship"})

_METADATA_FIELDS = frozenset({"last_verified_at", "context_tags", "aliases"})


class ConflictTier(str, Enum):
    METADATA = "metadata"
    SUMMARY = "summary"
    SHARED_DIVERGENCE = "shared_divergence"


def tier_for_field(field_name: str) -> ConflictTier:
    """Map entity column / logical field to conflict tier."""
    if field_name == "summary":
        return ConflictTier.SUMMARY
    if field_name in _METADATA_FIELDS:
        return ConflictTier.METADATA
    return ConflictTier.SUMMARY


def requires_review(category: str) -> bool:
    """Shared-scope divergence categories that need admin review (LUM-514 vocabulary)."""
    return category in _REVIEW_CATEGORIES


def merge_metadata_field(field: str, existing: Any, incoming: Any) -> Any:
    """Merge metadata fields: newest-wins timestamps; set-union for tag/alias arrays."""
    if field == "last_verified_at":
        if existing is None:
            return incoming
        if incoming is None:
            return existing
        ex = existing if isinstance(existing, datetime) else _parse_dt(existing)
        inc = incoming if isinstance(incoming, datetime) else _parse_dt(incoming)
        if ex is None:
            return inc
        if inc is None:
            return ex
        return inc if inc >= ex else ex
    if field in ("context_tags", "aliases"):
        left = list(existing or [])
        right = list(incoming or [])
        return sorted(set(left) | set(right))
    return incoming


def format_represent_both_summary(contributions: list[tuple[str, str]]) -> str:
    """Build ``user_id: text · …`` sorted by user_id (LUM-514 string shape)."""
    ordered = sorted(contributions, key=lambda pair: pair[0])
    return " · ".join(f"{user_id}: {text}" for user_id, text in ordered)


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
