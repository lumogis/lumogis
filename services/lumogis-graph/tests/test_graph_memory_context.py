# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-124 Slice 2 — graph context hint suffix + ranking whitelist (lumogis-graph)."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

import config


def _patch_query_datetime_now(monkeypatch, gq_mod, fixed: datetime) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            del cls
            if tz is not None:
                return fixed.astimezone(tz)
            return fixed

    monkeypatch.setattr(gq_mod, "datetime", FixedDateTime)


class _Meta:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def fetch_all(self, query, params=None):
        del query, params
        return list(self._rows)

    def fetch_one(self, query, params=None):
        del query
        if not params:
            return self._rows[0] if self._rows else None
        eid = str(params[0])
        for r in self._rows:
            if str(r.get("entity_id")) == eid:
                return r
        return None

    def execute(self, query, params=None):
        del query, params


class _Graph:
    def ping(self):
        return True

    def create_node(self, labels, properties):
        del labels, properties
        return "n1"

    def create_edge(self, from_id, to_id, rel_type, properties):
        del from_id, to_id, rel_type, properties

    def query(self, cypher, params=None):
        del cypher, params
        return []


def test_rank_truncate_forwards_memory_columns(monkeypatch):
    from graph import query as gq

    monkeypatch.setattr(config, "get_graph_min_mention_count", lambda: 1)
    monkeypatch.setattr(config, "get_context_entity_budget", lambda: 10)
    merged = {
        "e1": {
            "row": {
                "entity_id": "e1",
                "name": "A",
                "entity_type": "PERSON",
                "aliases": [],
                "mention_count": 5,
                "updated_at": None,
                "memory_type": "correction",
                "last_verified_at": None,
            },
            "explicit": True,
            "sem_score": 0.0,
        }
    }
    out = gq._rank_and_truncate_entity_map(merged, "default")
    assert out[0]["memory_type"] == "correction"
    assert "last_verified_at" in out[0]


def test_context_graph_line_includes_frozen_hint_suffix(monkeypatch):
    from graph import query as gq

    row = {
        "entity_id": "e1",
        "name": "Ada",
        "entity_type": "PERSON",
        "aliases": [],
        "mention_count": 10,
        "updated_at": datetime(2024, 6, 1, tzinfo=timezone.utc),
        "memory_type": None,
        "last_verified_at": None,
    }
    meta = _Meta([row])
    monkeypatch.setattr(config, "get_metadata_store", lambda: meta)
    monkeypatch.setattr(config, "get_graph_store", lambda: _Graph())
    monkeypatch.setattr(config, "get_graph_min_mention_count", lambda: 1)
    monkeypatch.setattr(config, "get_context_entity_budget", lambda: 5)
    monkeypatch.setattr(config, "get_context_max_edges", lambda: 5)
    monkeypatch.setattr(config, "get_context_entity_semantic_enabled", lambda: False)

    def _ego(_gs, _eid, _uid, depth=1, limit=5):
        del depth, limit
        return {
            "edges": [
                {"neighbor_name": "Bob", "neighbor_type": "PERSON", "strength": 2},
            ]
        }

    monkeypatch.setattr(gq, "ego_network", _ego)

    frags: list[str] = []
    fixed = datetime(2024, 7, 1, tzinfo=timezone.utc)
    _patch_query_datetime_now(monkeypatch, gq, fixed)

    gq.on_context_building(query="Tell me about Ada", context_fragments=frags, user_id="default")
    assert len(frags) == 1
    assert " (hint: type=relationship; confidence=" in frags[0]
    assert "; last_seen=" in frags[0]


def test_edgeless_correction_emits_line(monkeypatch):
    from graph import query as gq

    row = {
        "entity_id": "e1",
        "name": "Fixme",
        "entity_type": "CONCEPT",
        "aliases": [],
        "mention_count": 4,
        "updated_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "memory_type": "correction",
        "last_verified_at": None,
    }
    meta = _Meta([row])
    monkeypatch.setattr(config, "get_metadata_store", lambda: meta)
    monkeypatch.setattr(config, "get_graph_store", lambda: _Graph())
    monkeypatch.setattr(config, "get_graph_min_mention_count", lambda: 1)
    monkeypatch.setattr(config, "get_context_entity_budget", lambda: 5)
    monkeypatch.setattr(config, "get_context_max_edges", lambda: 5)
    monkeypatch.setattr(config, "get_context_entity_semantic_enabled", lambda: False)

    monkeypatch.setattr(gq, "ego_network", lambda *a, **k: {"edges": []})

    _patch_query_datetime_now(monkeypatch, gq, datetime(2024, 7, 1, tzinfo=timezone.utc))

    frags: list[str] = []
    gq.on_context_building(query="Fixme entity", context_fragments=frags, user_id="default")
    assert len(frags) == 1
    assert "[no related entities]" in frags[0]
    assert " (hint: type=correction; confidence=" in frags[0]


def test_half_life_case_insensitive_and_file_bucket(monkeypatch):
    monkeypatch.setenv("LUMOGIS_ENTITY_HALFLIFE_PERSON_DAYS", "90")
    monkeypatch.setenv("LUMOGIS_ENTITY_HALFLIFE_FILE_DAYS", "30")
    assert config.get_entity_half_life_days("person") == 90
    assert config.get_entity_half_life_days("FILE") == 30


def test_fetch_entity_row_for_context_sql_includes_visibility_and_entity_id(monkeypatch):
    """Regression: LUM-124 column add must not drop the WHERE clause (invalid SQL)."""
    from graph import query as gq

    captured: dict[str, object] = {}

    class _CaptureMeta:
        def fetch_one(self, query, params=None):
            captured["query"] = query
            captured["params"] = params
            return None

    monkeypatch.setattr(config, "get_metadata_store", lambda: _CaptureMeta())
    gq._fetch_entity_row_for_context("ent-42", "user-1")

    query = str(captured.get("query", ""))
    assert "WHERE" in query
    assert "entity_id = %s" in query
    assert "is_staged IS NOT TRUE" in query
    assert captured.get("params") is not None
    assert captured["params"][-1] == "ent-42"


def test_unknown_entity_type_debug_once(monkeypatch, caplog):
    import logging

    config._unknown_entity_halflife_types.discard("weirdthing")
    monkeypatch.delenv("LUMOGIS_ENTITY_HALFLIFE_CONCEPT_DAYS", raising=False)
    caplog.set_level(logging.DEBUG)
    config.get_entity_half_life_days("WeirdThing")
    config.get_entity_half_life_days("WeirdThing")
    weird_logs = [r for r in caplog.records if "WeirdThing" in r.getMessage()]
    assert len(weird_logs) == 1
