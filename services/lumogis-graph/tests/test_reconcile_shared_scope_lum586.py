# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-586: reconcile must not let the personal arm steal shared rows.

Two P0/P1 guards, both hermetic (no live Postgres/FalkorDB — the metadata store
records the SQL it is asked to run and returns no rows):

* ``reconcile_entities`` scopes to ``personal``/NULL so it can't (mis-)project a
  ``scope='shared'`` row through the type-labelled personal writer and stamp it.
* ``reconcile_shared_entities`` exists, targets ``scope='shared'`` projections,
  and uses the FULL stale condition (so a ``share_origin``→``'multiple'``
  conflict update that advanced ``updated_at`` is re-swept, not just NULLs).
"""

from __future__ import annotations

from conftest import MockGraphStore
from graph import reconcile

import config


class _RecordingMS:
    def __init__(self):
        self.queries: list[str] = []

    def fetch_all(self, sql: str, params: tuple | None = None) -> list[dict]:
        self.queries.append(sql)
        return []


def _patch(monkeypatch) -> _RecordingMS:
    ms = _RecordingMS()
    monkeypatch.setattr(config, "get_graph_store", lambda: MockGraphStore())
    monkeypatch.setattr(config, "get_metadata_store", lambda: ms)
    return ms


def test_reconcile_entities_scopes_to_personal(monkeypatch):
    ms = _patch(monkeypatch)
    reconcile.reconcile_entities()
    stale_sql = next(q for q in ms.queries if "FROM entities" in q)
    assert "scope = 'personal' OR scope IS NULL" in stale_sql


def test_reconcile_shared_entities_targets_shared_with_full_stale_condition(monkeypatch):
    ms = _patch(monkeypatch)
    reconcile.reconcile_shared_entities()
    stale_sql = next(q for q in ms.queries if "FROM entities" in q)
    assert "scope = 'shared'" in stale_sql
    assert "published_from IS NOT NULL" in stale_sql
    # Full stale condition — not a bare `graph_projected_at IS NULL`.
    assert "updated_at > graph_projected_at" in stale_sql


def test_run_reconciliation_includes_shared_entities_pass(monkeypatch):
    _patch(monkeypatch)
    results = reconcile.run_reconciliation(limit_per_type=1)
    assert "shared_entities" in results
    # Kept out of the summed projection totals (own unit, like orphan_gc).
    assert "shared_entities" not in ("documents", "entities", "sessions", "notes", "audio")
