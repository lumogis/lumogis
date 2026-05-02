# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Heuristic entity quality scoring — Pass 1 of the KG Quality Pipeline.

Scores each extracted entity on five signals and routes it into one of three
tiers based on configurable thresholds:

  extraction_quality < ENTITY_QUALITY_LOWER            → discard
  ENTITY_QUALITY_LOWER <= extraction_quality < UPPER   → staged (is_staged=True)
  extraction_quality >= ENTITY_QUALITY_UPPER           → normal (is_staged=False)

Caller: services/entities.py store_entities() — this is the only call site.
"""

import logging
import re

from models.entities import ExtractedEntity

import config

_log = logging.getLogger(__name__)

# Leading determiners that significantly lower the quality of an entity name.
_DETERMINERS = frozenset({"the", "a", "an", "this", "that", "these", "those"})

# Sentence-initial capital: first token only is title-case and length > 1
_RE_WORD = re.compile(r"\S+")


# ---------------------------------------------------------------------------
# Signal sub-scorers
# ---------------------------------------------------------------------------


def _score_stop_absence(name_lower: str) -> float:
    """1.0 if name is not in the stop set; else 0.0."""
    stop_set = config.get_stop_entity_set()
    return 0.0 if name_lower in stop_set else 1.0


def _score_capitalisation(name: str) -> float:
    """1.0 any non-first token is title-case or ALLCAPS; 0.5 only first-token cap; 0.2 all lower."""
    tokens = _RE_WORD.findall(name)
    if not tokens:
        return 0.2

    def _is_title_or_allcaps(tok: str) -> bool:
        if len(tok) < 2:
            return False
        return tok[0].isupper() and (tok.isupper() or tok[0].isupper() and tok[1:].islower())

    # Check tokens beyond the first (any strong capitalisation signal beyond sentence start)
    if len(tokens) >= 2:
        for tok in tokens[1:]:
            if len(tok) >= 2 and (tok[0].isupper()):
                return 1.0

    # Single-token or only first token is capitalised
    if _is_title_or_allcaps(tokens[0]):
        return 0.5

    return 0.2


def _score_determiner_absence(name: str) -> float:
    """1.0 if name does NOT start with a leading determiner; 0.35 if it does."""
    tokens = _RE_WORD.findall(name)
    if not tokens:
        return 1.0
    first = tokens[0].lower()
    return 0.35 if first in _DETERMINERS else 1.0


def _score_length_sanity(name: str) -> float:
    """1.0 for 2 <= len <= 120 and not pure digits; else scale toward 0."""
    stripped = name.strip()
    length = len(stripped)
    if length < 2:
        return 0.0
    if stripped.isdigit():
        return 0.0
    if length > 120:
        # Linearly decay from 1.0 at 120 to 0 at 240+
        return max(0.0, 1.0 - (length - 120) / 120)
    return 1.0


def _score_multi_token(name: str) -> float:
    """1.0 if >= 2 whitespace-separated tokens; 0.6 otherwise."""
    return 1.0 if len(name.split()) >= 2 else 0.6


# ---------------------------------------------------------------------------
# Composite scorer
# ---------------------------------------------------------------------------

_WEIGHTS = (
    (0.35, _score_stop_absence),
    (0.25, _score_capitalisation),
    (0.15, _score_determiner_absence),
    (0.15, _score_length_sanity),
    (0.10, _score_multi_token),
)

# Sanity check: weights must sum to 1.0
assert abs(sum(w for w, _ in _WEIGHTS) - 1.0) < 1e-9, "Quality signal weights must sum to 1.0"


def _compute_quality(name: str) -> float:
    """Return extraction_quality in [0, 1] for a single entity name.

    Stop list membership hard-clamps the score to 0.0: a phrase in the stop list
    is always discarded regardless of other signal values.  This preserves the
    semantic intent of the stop list (hard exclusion) while keeping the weighted
    formula for all other entities.
    """
    name_lower = name.lower().strip()
    # Stop list is a hard gate: any match → score = 0.0 (always discard).
    if not _score_stop_absence(name_lower):
        return 0.0
    score = 0.35  # stop_absence weight * 1.0 (confirmed not in stop list)
    score += 0.25 * _score_capitalisation(name)
    score += 0.15 * _score_determiner_absence(name)
    score += 0.15 * _score_length_sanity(name)
    score += 0.10 * _score_multi_token(name)
    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def score_and_filter_entities(
    entities: list[ExtractedEntity],
    user_id: str,
) -> tuple[list[ExtractedEntity], int]:
    """Score each entity and route to discard / staged / normal tier.

    Returns (kept_entities, discarded_count).
    Each kept entity has extraction_quality and is_staged set in-memory.
    The original list is not mutated; new attribute values are set on each object.
    """
    lower_threshold = config.get_entity_quality_lower()
    upper_threshold = config.get_entity_quality_upper()
    fail_open = config.get_entity_quality_fail_open()

    # Check stop list mtime once per call (O(1) on the hot path)
    config.get_stop_entity_set()

    try:
        kept: list[ExtractedEntity] = []
        discarded = 0

        for entity in entities:
            quality = _compute_quality(entity.name)
            if quality < lower_threshold:
                discarded += 1
                continue
            entity.extraction_quality = quality  # type: ignore[attr-defined]
            entity.is_staged = quality < upper_threshold  # type: ignore[attr-defined]
            kept.append(entity)

        return kept, discarded

    except Exception:
        if fail_open:
            _log.warning(
                "entity_quality: scorer raised an exception for user=%s — "
                "returning original entity list unchanged (ENTITY_QUALITY_FAIL_OPEN=true)",
                user_id,
                exc_info=True,
            )
            for entity in entities:
                entity.extraction_quality = None  # type: ignore[attr-defined]
                entity.is_staged = None  # type: ignore[attr-defined]
            return entities, 0
        else:
            _log.error(
                "entity_quality: scorer raised an exception for user=%s — "
                "discarding entire batch (ENTITY_QUALITY_FAIL_OPEN=false)",
                user_id,
                exc_info=True,
            )
            return [], len(entities)
