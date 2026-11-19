# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-586: the KG-side DOCUMENT_SHARED handler (graph.writer.on_document_shared).

Hermetic — ``projection.project_entity_into_graph`` and the graph-projected-at
stamp are stubbed at the port. Pins: field mapping from the webhook refs, the
skip of malformed refs, and that ``graph_projected_at`` is stamped ONLY on a
successful projection (so a failed sweep is retried by the reconcile backstop).
"""

from __future__ import annotations

import projection
from conftest import MockGraphStore
from graph import writer

import config


class _NoTombstones:
    """A store whose ``entity_deletions`` lookup finds nothing (LUM-966: no entry was deleted)."""

    def fetch_one(self, sql, params=None):
        return None


def _wire(monkeypatch, *, project_result=True):
    monkeypatch.setattr(config, "get_graph_store", lambda: MockGraphStore())
    monkeypatch.setattr(config, "get_metadata_store", lambda: _NoTombstones())

    calls: list[dict] = []

    def _fake_project(**kw):
        calls.append(kw)
        return project_result

    monkeypatch.setattr(projection, "project_entity_into_graph", _fake_project)

    stamped: list[str] = []
    monkeypatch.setattr(
        writer,
        "_stamp_graph_projected_at",
        lambda table, key, val, user_id, ms=None: stamped.append(val),
    )
    return calls, stamped


def test_on_document_shared_projects_each_ref(monkeypatch):
    calls, stamped = _wire(monkeypatch)
    writer.on_document_shared(
        file_path="/d.pdf",
        user_id="alice",
        entities=[
            {"src_entity_id": "e1", "proj_entity_id": "s1", "name": "Ada", "entity_type": "PERSON"},
            {"src_entity_id": "e2", "proj_entity_id": "s2", "name": "Bab", "entity_type": "PERSON"},
        ],
    )
    assert [c["src_entity_id"] for c in calls] == ["e1", "e2"]
    assert [c["proj_entity_id"] for c in calls] == ["s1", "s2"]
    assert all(c["target_scope"] == "shared" and c["actor_user_id"] == "alice" for c in calls)
    assert stamped == ["s1", "s2"]


def test_on_document_shared_skips_refs_missing_ids(monkeypatch):
    calls, stamped = _wire(monkeypatch)
    writer.on_document_shared(
        file_path="/d.pdf",
        user_id="alice",
        entities=[
            {"src_entity_id": "e1", "proj_entity_id": "", "name": "X", "entity_type": "PERSON"},
            {"proj_entity_id": "s2", "name": "Y", "entity_type": "PERSON"},
            {"src_entity_id": "e3", "proj_entity_id": "s3", "name": "Z", "entity_type": "PERSON"},
        ],
    )
    assert [c["proj_entity_id"] for c in calls] == ["s3"]
    assert stamped == ["s3"]


def test_on_document_shared_does_not_stamp_on_projection_failure(monkeypatch):
    calls, stamped = _wire(monkeypatch, project_result=False)
    writer.on_document_shared(
        file_path="/d.pdf",
        user_id="alice",
        entities=[
            {"src_entity_id": "e1", "proj_entity_id": "s1", "name": "Ada", "entity_type": "PERSON"},
        ],
    )
    assert len(calls) == 1
    assert stamped == [], "unstamped rows are retried by reconcile_shared_entities"


def test_on_document_shared_noop_without_graph_store(monkeypatch):
    monkeypatch.setattr(config, "get_graph_store", lambda: None)
    called = {"n": 0}

    def _fake_project(**kw):
        called["n"] += 1
        return True

    monkeypatch.setattr(projection, "project_entity_into_graph", _fake_project)
    writer.on_document_shared(
        file_path="/d.pdf",
        user_id="alice",
        entities=[{"src_entity_id": "e1", "proj_entity_id": "s1", "name": "A", "entity_type": "P"}],
    )
    assert called["n"] == 0
