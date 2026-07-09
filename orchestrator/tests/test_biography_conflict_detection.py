# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for biography conflict detection policy (LUM-514)."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from uuid import uuid4

import pytest
from models.biography_conflict import BiographyPinSnapshot
from models.biography_conflict import ConflictResolutionRequest
from services.biography_conflict import apply_resolution_with_pins
from services.biography_conflict import detect_conflicts
from services.biography_conflict import open_conflict_resolution_stub

_TS = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)


def _pin(
    *,
    user_id: str,
    text: str,
    category: str = "logistics",
    scope: str = "shared",
    subject_text: str = "dinner time",
    subject_entity_id=None,
) -> BiographyPinSnapshot:
    return BiographyPinSnapshot(
        id=uuid4(),
        user_id=user_id,
        text=text,
        category=category,
        domain="household",
        scope=scope,
        subject_entity_id=subject_entity_id,
        subject_text=subject_text,
        updated_at=_TS,
    )


def test_equivalent_pins_deduped() -> None:
    alice = _pin(user_id="alice", text="18:00")
    bob = _pin(user_id="bob", text="  18:00  ")
    assert detect_conflicts([alice, bob]) == []


def test_divergent_pins_create_conflict() -> None:
    alice = _pin(user_id="alice", text="18:00")
    bob = _pin(user_id="bob", text="19:00")
    conflicts = detect_conflicts([alice, bob])
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.requires_review is True
    assert c.represent_both_line == "alice: 18:00 · bob: 19:00"
    assert len(c.contributions) == 2


def test_subjectless_pin_not_eligible() -> None:
    pin = BiographyPinSnapshot(
        id=uuid4(),
        user_id="alice",
        text="18:00",
        category="logistics",
        domain="household",
        scope="shared",
        subject_entity_id=None,
        subject_text=None,
        updated_at=_TS,
    )
    assert detect_conflicts([pin]) == []


def test_personal_pins_excluded() -> None:
    personal = _pin(user_id="alice", text="secret", scope="personal")
    shared_a = _pin(user_id="alice", text="18:00")
    shared_b = _pin(user_id="bob", text="19:00")
    conflicts = detect_conflicts([personal, shared_a, shared_b])
    assert len(conflicts) == 1
    user_ids = {c.user_id for c in conflicts[0].contributions}
    assert user_ids == {"alice", "bob"}
    assert "secret" not in conflicts[0].represent_both_line


def test_identity_pins_never_conflict() -> None:
    alice = _pin(user_id="alice", text="she/her", category="identity")
    bob = _pin(user_id="bob", text="he/him", category="identity")
    assert detect_conflicts([alice, bob]) == []


def test_single_user_noop() -> None:
    a = _pin(user_id="alice", text="18:00")
    b = _pin(user_id="alice", text="19:00")
    assert detect_conflicts([a, b]) == []


def test_preference_no_review_flag() -> None:
    alice = _pin(user_id="alice", text="tea", category="preference", subject_text="drink")
    bob = _pin(user_id="bob", text="coffee", category="preference", subject_text="drink")
    conflicts = detect_conflicts([alice, bob])
    assert len(conflicts) == 1
    assert conflicts[0].requires_review is False


def test_confirm_one_archives_loser() -> None:
    pin_a, pin_b = uuid4(), uuid4()
    open_row = open_conflict_resolution_stub(fact_group_key="k")
    req = ConflictResolutionRequest(action="confirm_one", chosen_pin_id=pin_a)
    resolved = apply_resolution_with_pins(
        open_row,
        [pin_a, pin_b],
        req,
        resolved_by="admin",
    )
    assert resolved.status == "resolved"
    assert resolved.archived_pin_ids == [pin_b]


def test_keep_both_resolved_empty_archive() -> None:
    open_row = open_conflict_resolution_stub(fact_group_key="k")
    req = ConflictResolutionRequest(action="keep_both")
    resolved = apply_resolution_with_pins(
        open_row,
        [uuid4(), uuid4()],
        req,
        resolved_by="admin",
    )
    assert resolved.status == "resolved"
    assert resolved.archived_pin_ids == []


def test_dismiss_sets_dismissed() -> None:
    open_row = open_conflict_resolution_stub(fact_group_key="k")
    req = ConflictResolutionRequest(action="dismiss")
    resolved = apply_resolution_with_pins(
        open_row,
        [uuid4(), uuid4()],
        req,
        resolved_by="admin",
    )
    assert resolved.status == "dismissed"
    assert resolved.archived_pin_ids == []


def test_confirm_one_missing_chosen_id_raises() -> None:
    open_row = open_conflict_resolution_stub(fact_group_key="k")
    req = ConflictResolutionRequest(action="confirm_one")
    with pytest.raises(ValueError, match="chosen_pin_id"):
        apply_resolution_with_pins(open_row, [uuid4()], req, resolved_by="admin")


def test_chosen_pin_not_in_conflict_raises() -> None:
    open_row = open_conflict_resolution_stub(fact_group_key="k")
    req = ConflictResolutionRequest(action="confirm_one", chosen_pin_id=uuid4())
    with pytest.raises(ValueError, match="not in conflict"):
        apply_resolution_with_pins(open_row, [uuid4()], req, resolved_by="admin")
