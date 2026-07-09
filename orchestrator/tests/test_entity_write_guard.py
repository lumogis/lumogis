# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for entity summary OCC write guard (LUM-358)."""

from __future__ import annotations

from uuid import uuid4

import pytest

import config
from auth import UserContext
from services import entity_write_guard as guard
from services.consolidation_lock import advisory_key2
from services.consolidation_lock import resolve_scope_owner


class FakeMetadataStore:
    """Minimal fake matching premium temporal pipeline tests."""

    def __init__(self):
        self.fetches: list[tuple[str, tuple]] = []
        self._one: list[tuple[str, dict | None]] = []

    def seed_one(self, substring: str, row: dict | None) -> None:
        self._one.append((substring, row))

    def fetch_one(self, sql: str, params=None):
        self.fetches.append((sql, params))
        for substring, row in self._one:
            if substring in sql:
                return dict(row) if row else None
        return None


@pytest.fixture
def ms():
    return FakeMetadataStore()


@pytest.fixture
def wire_ms(monkeypatch, ms):
    monkeypatch.setattr(config, "get_metadata_store", lambda: ms)
    return ms


def _caller(user_id: str = "alice") -> UserContext:
    return UserContext(user_id=user_id, is_authenticated=True, role="member")


def test_commit_summary_success(wire_ms):
    eid = uuid4()
    wire_ms.seed_one(
        "FROM entities",
        {
            "entity_id": eid,
            "user_id": "alice",
            "scope": "personal",
            "entity_type": "PERSON",
            "summary": "old",
            "staged_summary": None,
            "version": 3,
        },
    )
    wire_ms.seed_one("RETURNING version", {"version": 4})

    result = guard.commit_summary_update(
        eid,
        caller=_caller("alice"),
        read_version=3,
        new_summary="new text",
    )
    assert result.ok is True
    assert result.conflict is False
    assert result.new_version == 4


def test_commit_summary_conflict(wire_ms):
    eid = uuid4()
    wire_ms.seed_one(
        "FROM entities",
        {
            "entity_id": eid,
            "user_id": "alice",
            "scope": "personal",
            "entity_type": "PERSON",
            "summary": "v4",
            "staged_summary": None,
            "version": 4,
        },
    )
    wire_ms.seed_one("RETURNING version", None)

    result = guard.commit_summary_update(
        eid,
        caller=_caller("alice"),
        read_version=3,
        new_summary="lost race",
    )
    assert result.ok is False
    assert result.conflict is True
    assert result.new_version is None


def test_read_entity_summary_hides_other_users_personal(wire_ms):
    wire_ms.seed_one("FROM entities", None)
    result = guard.read_entity_summary(uuid4(), caller=_caller("alice"))
    assert result is None


def test_read_entity_summary_returns_shared(wire_ms):
    eid = uuid4()
    wire_ms.seed_one(
        "FROM entities",
        {
            "entity_id": eid,
            "user_id": "bob",
            "scope": "shared",
            "entity_type": "PERSON",
            "summary": "household fact",
            "staged_summary": None,
            "version": 1,
        },
    )
    snap = guard.read_entity_summary(eid, caller=_caller("alice"))
    assert snap is not None
    assert snap.scope == "shared"
    assert snap.summary == "household fact"


def test_commit_shared_scope_does_not_require_row_user_id_match(wire_ms):
    eid = uuid4()
    wire_ms.seed_one(
        "FROM entities",
        {
            "entity_id": eid,
            "user_id": "bob",
            "scope": "shared",
            "entity_type": "PERSON",
            "summary": "initial",
            "staged_summary": None,
            "version": 2,
        },
    )
    wire_ms.seed_one("RETURNING version", {"version": 3})

    result = guard.commit_summary_update(
        eid,
        caller=_caller("alice"),
        read_version=2,
        new_summary="alice edit",
    )
    assert result.ok is True


def test_commit_system_scope_rejected(wire_ms):
    eid = uuid4()
    wire_ms.seed_one(
        "FROM entities",
        {
            "entity_id": eid,
            "user_id": "default",
            "scope": "system",
            "entity_type": "CONCEPT",
            "summary": "system",
            "staged_summary": None,
            "version": 1,
        },
    )
    result = guard.commit_summary_update(
        eid,
        caller=_caller("alice"),
        read_version=1,
        new_summary="nope",
    )
    assert result.ok is False
    assert result.conflict is False


def test_promote_staged_summary_when_null(wire_ms):
    eid = uuid4()
    wire_ms.seed_one(
        "FROM entities",
        {
            "entity_id": eid,
            "user_id": "alice",
            "scope": "personal",
            "entity_type": "PERSON",
            "summary": "live",
            "staged_summary": None,
            "version": 1,
        },
    )
    wire_ms.seed_one("RETURNING version", None)
    result = guard.promote_staged_summary(eid, caller=_caller("alice"), read_version=1)
    assert result.ok is False
    assert result.conflict is True


def test_resolve_scope_owner_personal():
    assert resolve_scope_owner(user_id="alice", scope="personal") == "alice"


def test_resolve_scope_owner_shared():
    assert resolve_scope_owner(user_id="alice", scope="shared") == "household"


def test_try_acquire_consolidation_lock_empty_entity_type():
    from services.consolidation_lock import try_acquire_consolidation_lock

    with pytest.raises(ValueError):
        advisory_key2("alice", "")

    with pytest.raises(ValueError):
        try_acquire_consolidation_lock("alice", "")
