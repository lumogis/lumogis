# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Household biography conflict policy — Option C (LUM-514).

Pure detection and resolution state transitions. No I/O.

Contracts (also in ``docs/private/specs/biography-conflict-acceptance.md``):

* **fact_group_key** — ``(category, domain, subject_key)`` where ``subject_key`` is
  normalised ``subject_entity_id`` (UUID string) when present, else normalised
  ``subject_text`` (NFKC, lowercased, trimmed). Pins with neither subject field
  are not conflict-eligible.
* **Single-user no-op** — groups with ≤1 distinct ``user_id`` produce no conflicts.
* **Identity exempt** — ``category=identity`` never participates.
* **Represent-both** — default for all conflict-eligible categories; ``requires_review``
  additionally true for ``logistics``, ``focus``, and ``relationship``.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from datetime import datetime
from datetime import timezone
from uuid import UUID
from uuid import uuid4

from models.biography_conflict import BiographyPinSnapshot
from models.biography_conflict import ConflictContribution
from models.biography_conflict import ConflictResolution
from models.biography_conflict import ConflictResolutionRequest
from models.biography_conflict import DetectedConflict

_REVIEW_CATEGORIES = frozenset({"logistics", "focus", "relationship"})


def _normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _subject_key(pin: BiographyPinSnapshot) -> str | None:
    if pin.subject_entity_id is not None:
        return str(pin.subject_entity_id).lower()
    if pin.subject_text is not None:
        stripped = pin.subject_text.strip()
        if stripped:
            return _normalize_text(stripped)
    return None


def is_conflict_eligible(pin: BiographyPinSnapshot) -> bool:
    """Shared-scope pins with a subject, excluding identity category."""
    if pin.scope != "shared":
        return False
    if pin.category == "identity":
        return False
    return _subject_key(pin) is not None


def fact_group_key(pin: BiographyPinSnapshot) -> str:
    """Stable grouping key: category | domain | subject_key."""
    subject = _subject_key(pin)
    if subject is None:
        raise ValueError("pin is not conflict-eligible — no subject key")
    domain = pin.domain or ""
    return f"{pin.category}|{domain}|{subject}"


def _requires_review(category: str) -> bool:
    return category in _REVIEW_CATEGORIES


def format_represent_both(contributions: list[ConflictContribution]) -> str:
    """Build ``user_id: text · …`` with contributions sorted by user_id ASC."""
    ordered = sorted(contributions, key=lambda c: c.user_id)
    return " · ".join(f"{c.user_id}: {c.text}" for c in ordered)


def detect_conflicts(pins: list[BiographyPinSnapshot]) -> list[DetectedConflict]:
    """Detect divergent shared-scope pins among multiple household authors."""
    eligible = [p for p in pins if is_conflict_eligible(p)]
    groups: dict[str, list[BiographyPinSnapshot]] = defaultdict(list)
    for pin in eligible:
        groups[fact_group_key(pin)].append(pin)

    conflicts: list[DetectedConflict] = []
    for key, group_pins in groups.items():
        distinct_users = {p.user_id for p in group_pins}
        if len(distinct_users) <= 1:
            continue

        normalized_texts = {_normalize_text(p.text) for p in group_pins}
        if len(normalized_texts) <= 1:
            continue

        contributions = [
            ConflictContribution(
                user_id=p.user_id,
                pin_id=p.id,
                text=p.text,
                updated_at=p.updated_at,
            )
            for p in group_pins
        ]
        category = group_pins[0].category
        domain = group_pins[0].domain
        conflicts.append(
            DetectedConflict(
                fact_group_key=key,
                category=category,
                domain=domain,
                pin_ids=[p.id for p in group_pins],
                contributions=contributions,
                requires_review=_requires_review(category),
                represent_both_line=format_represent_both(contributions),
            )
        )
    return conflicts


def apply_resolution(
    conflict: ConflictResolution,
    request: ConflictResolutionRequest,
    *,
    resolved_by: str,
) -> ConflictResolution:
    """Pure state transition for an open conflict row (no I/O)."""
    if conflict.status != "open":
        raise ValueError("conflict is not open")

    now = datetime.now(timezone.utc)
    action = request.action

    if action == "confirm_one":
        if request.chosen_pin_id is None:
            raise ValueError("chosen_pin_id required for confirm_one")
        archived = [pid for pid in conflict.archived_pin_ids if pid != request.chosen_pin_id]
        # archived_pin_ids on open row is empty; losers come from pin_ids on detection
        # Caller/store merges pin_ids from the open row — pass via conflict extension.
        return conflict.model_copy(
            update={
                "status": "resolved",
                "action": action,
                "resolved_by": resolved_by,
                "resolved_at": now,
                "archived_pin_ids": archived,
            }
        )

    if action == "keep_both":
        return conflict.model_copy(
            update={
                "status": "resolved",
                "action": action,
                "resolved_by": resolved_by,
                "resolved_at": now,
                "archived_pin_ids": [],
            }
        )

    if action == "dismiss":
        return conflict.model_copy(
            update={
                "status": "dismissed",
                "action": action,
                "resolved_by": resolved_by,
                "resolved_at": now,
                "archived_pin_ids": [],
            }
        )

    raise ValueError(f"unknown action: {action}")


def apply_resolution_with_pins(
    conflict: ConflictResolution,
    pin_ids: list[UUID],
    request: ConflictResolutionRequest,
    *,
    resolved_by: str,
) -> ConflictResolution:
    """``apply_resolution`` with pin_ids from the open conflict row."""
    if request.action == "confirm_one":
        if request.chosen_pin_id is None:
            raise ValueError("chosen_pin_id required for confirm_one")
        if request.chosen_pin_id not in pin_ids:
            raise ValueError("chosen_pin_id not in conflict pin_ids")
        archived = [pid for pid in pin_ids if pid != request.chosen_pin_id]
        now = datetime.now(timezone.utc)
        return conflict.model_copy(
            update={
                "status": "resolved",
                "action": request.action,
                "resolved_by": resolved_by,
                "resolved_at": now,
                "archived_pin_ids": archived,
            }
        )
    return apply_resolution(conflict, request, resolved_by=resolved_by)


def open_conflict_resolution_stub(
    *,
    conflict_id: UUID | None = None,
    fact_group_key: str,
    pin_ids: list[UUID] | None = None,
) -> ConflictResolution:
    """Build an open ``ConflictResolution`` shell for tests and store seeding."""
    return ConflictResolution(
        id=conflict_id or uuid4(),
        fact_group_key=fact_group_key,
        status="open",
        archived_pin_ids=pin_ids or [],
    )
