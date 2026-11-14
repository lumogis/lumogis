# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for orchestrator/services/document_entity_cascade.py (LUM-586).

These pin the *decision logic* of the document → entity share cascade and its
refcounted retraction planner without any live Postgres/Qdrant/FalkorDB: the
metadata store is a hand-injected fake, ``services.projection`` is stubbed at
the port, and the outbound ``DOCUMENT_SHARED`` fire is captured. The live-stack
behaviour is proven by the integration suite
(``services/lumogis-graph/tests/test_document_shared_graph_integration.py``).
"""

from __future__ import annotations

import hooks
import pytest
from auth import UserContext
from events import Event

import config
from services import document_entity_cascade as cascade
from services import projection


class _FakeMS:
    """Minimal metadata-store stand-in returning canned ``fetch_all`` rows."""

    def __init__(self, rows: list[dict] | None = None):
        self._rows = rows or []
        self.executed: list[tuple[str, tuple]] = []

    def fetch_all(self, sql: str, params: tuple | None = None) -> list[dict]:
        return list(self._rows)

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self.executed.append((sql, params))


@pytest.fixture
def actor() -> UserContext:
    return UserContext(user_id="alice", is_authenticated=True)


@pytest.fixture(autouse=True)
def _graph_enabled(monkeypatch):
    """Default every test to a non-disabled graph mode; opt out per-test."""
    monkeypatch.setattr(config, "get_graph_mode", lambda: "service")


# ---------------------------------------------------------------------------
# Mode gating
# ---------------------------------------------------------------------------


def test_cascade_noop_when_graph_disabled(monkeypatch, actor):
    monkeypatch.setattr(config, "get_graph_mode", lambda: "disabled")
    fired: list = []
    monkeypatch.setattr(hooks, "fire_background", lambda *a, **k: fired.append((a, k)))
    # If the gate is wrong this would call get_metadata_store; make that explode.
    monkeypatch.setattr(config, "get_metadata_store", _boom)

    assert cascade.cascade_share_document_entities(
        src_file={"file_path": "/d.pdf", "user_id": "alice"}, actor=actor
    ) == (0, 0)
    assert fired == [], "no DOCUMENT_SHARED fire when graph disabled"


def test_graph_feature_enabled_reflects_mode(monkeypatch):
    monkeypatch.setattr(config, "get_graph_mode", lambda: "service")
    assert cascade._graph_feature_enabled() is True
    monkeypatch.setattr(config, "get_graph_mode", lambda: "inprocess")
    assert cascade._graph_feature_enabled() is True
    monkeypatch.setattr(config, "get_graph_mode", lambda: "disabled")
    assert cascade._graph_feature_enabled() is False


def _boom(*_a, **_k):
    raise AssertionError("metadata store must not be touched when disabled")


# ---------------------------------------------------------------------------
# cascade_share_document_entities
# ---------------------------------------------------------------------------


def test_cascade_rejects_foreign_owner(monkeypatch, actor):
    monkeypatch.setattr(config, "get_metadata_store", lambda: _FakeMS())
    with pytest.raises(ValueError, match="owner does not match actor"):
        cascade.cascade_share_document_entities(
            src_file={"file_path": "/d.pdf", "user_id": "bob"}, actor=actor
        )


def test_cascade_noop_when_no_extracted_entities(monkeypatch, actor):
    monkeypatch.setattr(config, "get_metadata_store", lambda: _FakeMS(rows=[]))
    fired: list = []
    monkeypatch.setattr(hooks, "fire_background", lambda *a, **k: fired.append((a, k)))
    assert cascade.cascade_share_document_entities(
        src_file={"file_path": "/d.pdf", "user_id": "alice"}, actor=actor
    ) == (0, 0)
    assert fired == []


def test_cascade_projects_each_entity_with_document_origin(monkeypatch, actor):
    rows = [
        {"entity_id": "e1", "name": "Ada", "entity_type": "PERSON"},
        {"entity_id": "e2", "name": "Babbage", "entity_type": "PERSON"},
    ]
    monkeypatch.setattr(config, "get_metadata_store", lambda: _FakeMS(rows=rows))
    calls: list[dict] = []

    def _fake_project(src, *, target_scope, actor, share_origin="user"):
        calls.append({"src": src, "target_scope": target_scope, "share_origin": share_origin})
        return {"entity_id": f"shared::{src['entity_id']}"}

    monkeypatch.setattr(projection, "project_entity", _fake_project)
    fired: list = []
    monkeypatch.setattr(hooks, "fire_background", lambda ev, **k: fired.append((ev, k)))

    projected, failed = cascade.cascade_share_document_entities(
        src_file={"file_path": "/d.pdf", "user_id": "alice"}, actor=actor
    )

    assert (projected, failed) == (2, 0)
    assert [c["share_origin"] for c in calls] == ["document", "document"]
    assert all(c["target_scope"] == "shared" for c in calls)
    assert len(fired) == 1
    ev, kw = fired[0]
    assert ev == Event.DOCUMENT_SHARED
    assert kw["file_path"] == "/d.pdf"
    assert kw["user_id"] == "alice"
    ids = {e["proj_entity_id"] for e in kw["entities"]}
    assert ids == {"shared::e1", "shared::e2"}


def test_cascade_partial_on_projection_failure(monkeypatch, actor):
    rows = [
        {"entity_id": "e1", "name": "Ada", "entity_type": "PERSON"},
        {"entity_id": "e2", "name": "Boom", "entity_type": "PERSON"},
    ]
    monkeypatch.setattr(config, "get_metadata_store", lambda: _FakeMS(rows=rows))

    def _fake_project(src, *, target_scope, actor, share_origin="user"):
        if src["entity_id"] == "e2":
            raise RuntimeError("qdrant down")
        return {"entity_id": f"shared::{src['entity_id']}"}

    monkeypatch.setattr(projection, "project_entity", _fake_project)
    fired: list = []
    monkeypatch.setattr(hooks, "fire_background", lambda ev, **k: fired.append((ev, k)))

    projected, failed = cascade.cascade_share_document_entities(
        src_file={"file_path": "/d.pdf", "user_id": "alice"}, actor=actor
    )

    assert (projected, failed) == (1, 1)
    # Only the successfully projected entity is on the wire.
    assert len(fired) == 1
    ids = {e["proj_entity_id"] for e in fired[0][1]["entities"]}
    assert ids == {"shared::e1"}


def test_cascade_batches_large_entity_sets(monkeypatch, actor):
    rows = [
        {"entity_id": f"e{i}", "name": f"n{i}", "entity_type": "PERSON"}
        for i in range(cascade._SHARE_BATCH + 5)
    ]
    monkeypatch.setattr(config, "get_metadata_store", lambda: _FakeMS(rows=rows))
    monkeypatch.setattr(
        projection,
        "project_entity",
        lambda src, **k: {"entity_id": f"s::{src['entity_id']}"},
    )
    fired: list = []
    monkeypatch.setattr(hooks, "fire_background", lambda ev, **k: fired.append((ev, k)))

    projected, failed = cascade.cascade_share_document_entities(
        src_file={"file_path": "/d.pdf", "user_id": "alice"}, actor=actor
    )

    assert (projected, failed) == (cascade._SHARE_BATCH + 5, 0)
    assert len(fired) == 2, "entities beyond the batch cap dispatch in a second fire"
    assert len(fired[0][1]["entities"]) == cascade._SHARE_BATCH
    assert len(fired[1][1]["entities"]) == 5


# ---------------------------------------------------------------------------
# plan_document_entity_retraction — refcount decision matrix
# ---------------------------------------------------------------------------


def _plan_rows(*specs) -> list[dict]:
    """specs: (entity_id, share_origin, other_justification)."""
    return [
        {"src_entity_id": eid, "share_origin": origin, "other_justification": other}
        for (eid, origin, other) in specs
    ]


def test_plan_retracts_only_document_origin_without_other_justification():
    ms = _FakeMS(
        rows=_plan_rows(
            ("doc-only", "document", False),
            ("user-share", "user", False),
            ("null-legacy", None, False),
            ("multi", "multiple", False),
        )
    )
    retract, downgrade = cascade.plan_document_entity_retraction(ms, "/d.pdf", "alice")
    assert retract == ["doc-only"]
    assert downgrade == ["multi"]


def test_plan_keeps_everything_still_justified_by_another_shared_doc():
    ms = _FakeMS(
        rows=_plan_rows(
            ("doc-only", "document", True),
            ("multi", "multiple", True),
        )
    )
    retract, downgrade = cascade.plan_document_entity_retraction(ms, "/d.pdf", "alice")
    assert retract == []
    assert downgrade == []


def test_plan_never_touches_user_or_null_origin():
    ms = _FakeMS(rows=_plan_rows(("u", "user", False), ("n", None, False)))
    retract, downgrade = cascade.plan_document_entity_retraction(ms, "/d.pdf", "alice")
    assert retract == [] and downgrade == []


# ---------------------------------------------------------------------------
# retract_document_entities
# ---------------------------------------------------------------------------


def test_retract_noop_without_file_path(monkeypatch):
    monkeypatch.setattr(config, "get_metadata_store", _boom)
    assert cascade.retract_document_entities(file_path="", owner_user_id="alice") == 0


def test_retract_deletes_retract_ids_and_downgrades_multiple(monkeypatch):
    ms = _FakeMS(
        rows=_plan_rows(
            ("doc-only", "document", False),
            ("multi", "multiple", False),
        )
    )
    monkeypatch.setattr(config, "get_metadata_store", lambda: ms)
    unprojected: list[tuple[str, str]] = []

    def _fake_unproject(entity_id, scope):
        unprojected.append((entity_id, scope))
        return 1

    monkeypatch.setattr(projection, "unproject_entity", _fake_unproject)

    retracted = cascade.retract_document_entities(file_path="/d.pdf", owner_user_id="alice")

    assert retracted == 1
    assert unprojected == [("doc-only", "shared")]
    # The multiple → user downgrade ran exactly once against shared scope.
    assert len(ms.executed) == 1
    sql, params = ms.executed[0]
    assert "share_origin = 'user'" in sql
    assert params == (["multi"],)


def test_retract_noop_when_graph_disabled(monkeypatch):
    monkeypatch.setattr(config, "get_graph_mode", lambda: "disabled")
    monkeypatch.setattr(config, "get_metadata_store", _boom)
    assert cascade.retract_document_entities(file_path="/d.pdf", owner_user_id="alice") == 0


# ---------------------------------------------------------------------------
# LUM-604 — re-ingest removed-entity diff retraction
# ---------------------------------------------------------------------------


class _PruneFakeMS:
    """Returns canned prune candidates and records DELETE params."""

    def __init__(self, stale_ids: list[str]):
        self._stale = stale_ids
        self.executed: list[tuple[str, tuple]] = []

    def fetch_all(self, sql: str, params: tuple | None = None) -> list[dict]:
        if "NOT (source_id = ANY" in sql:
            return [{"source_id": sid} for sid in self._stale]
        return []

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self.executed.append((sql, params))


def test_prune_stale_document_relations_returns_and_deletes():
    ms = _PruneFakeMS(["gone-1", "gone-2"])
    removed = cascade.prune_stale_document_entity_relations(ms, "/d.pdf", "alice", ["keep-1"])
    assert removed == ["gone-1", "gone-2"]
    assert len(ms.executed) == 1
    assert "DELETE FROM entity_relations" in ms.executed[0][0]


def test_plan_reingest_removed_uses_same_origin_rules():
    ms = _FakeMS(
        rows=_plan_rows(
            ("doc-only", "document", False),
            ("multi", "multiple", False),
            ("still-ok", "document", True),
        )
    )
    retract, downgrade = cascade.plan_reingest_removed_entity_retraction(
        ms, "/d.pdf", "alice", ["doc-only", "multi", "still-ok"]
    )
    assert retract == ["doc-only"]
    assert downgrade == ["multi"]


def test_retract_removed_on_reingest_deletes_and_downgrades(monkeypatch):
    ms = _FakeMS(rows=_plan_rows(("doc-only", "document", False), ("multi", "multiple", False)))
    monkeypatch.setattr(config, "get_metadata_store", lambda: ms)
    unprojected: list[tuple[str, str]] = []

    monkeypatch.setattr(
        projection,
        "unproject_entity",
        lambda eid, scope: unprojected.append((eid, scope)) or 1,
    )

    retracted = cascade.retract_removed_entities_on_reingest(
        file_path="/d.pdf",
        owner_user_id="alice",
        removed_entity_ids=["doc-only", "multi"],
    )
    assert retracted == 1
    assert unprojected == [("doc-only", "shared")]
    assert len(ms.executed) == 1


def test_retract_removed_noop_without_removed_ids(monkeypatch):
    monkeypatch.setattr(config, "get_metadata_store", _boom)
    assert (
        cascade.retract_removed_entities_on_reingest(
            file_path="/d.pdf", owner_user_id="alice", removed_entity_ids=[]
        )
        == 0
    )


# ---------------------------------------------------------------------------
# projection.project_entity — share_origin contract (validated pre-DB)
# ---------------------------------------------------------------------------


def test_project_entity_rejects_bad_share_origin(actor):
    """Guards the ON CONFLICT provenance rule: only 'document'/'user' may reach
    the CASE. A stray value (e.g. 'multiple' passed by a caller) must fail loud
    rather than corrupt the refcount ('multiple' is only ever DB-derived)."""
    with pytest.raises(ValueError, match="share_origin must be"):
        projection.project_entity(
            {"entity_id": "e1", "name": "Ada", "entity_type": "PERSON"},
            target_scope="shared",
            actor=actor,
            share_origin="multiple",
        )


def test_project_entity_default_share_origin_is_user():
    """A direct LUM-581 share (no kwarg) must default to 'user', never NULL, so
    a later document cascade upgrades to 'multiple' instead of overwriting."""
    import inspect

    sig = inspect.signature(projection.project_entity)
    assert sig.parameters["share_origin"].default == "user"
