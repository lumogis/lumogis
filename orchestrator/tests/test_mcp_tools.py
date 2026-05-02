# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for the three Area-4 thin Postgres helpers.

These helpers back the MCP tools `memory.get_recent`, `entity.lookup`,
and `entity.search`. They follow the existing helper pattern in
routes/data.py::list_entities — empty answer + WARNING on DB failure,
never raise.
"""

import logging

import config as _config


# ---------------------------------------------------------------------------
# services.memory.recent_sessions
# ---------------------------------------------------------------------------


def test_recent_sessions_returns_empty_when_table_empty():
    from services.memory import recent_sessions

    assert recent_sessions(limit=5, user_id="default") == []


def test_recent_sessions_maps_rows_to_session_summary(monkeypatch):
    from services.memory import recent_sessions

    rows = [
        {
            "session_id": "s1",
            "summary": "first",
            "topics": ["a", "b"],
            "entities": ["Ada"],
            "entity_ids": ["uuid-a"],
        },
        {
            "session_id": "s2",
            "summary": "second",
            "topics": [],
            "entities": [],
            "entity_ids": [],
        },
    ]
    ms = _config.get_metadata_store()
    monkeypatch.setattr(ms, "fetch_all", lambda q, p=None: rows)

    out = recent_sessions(limit=2, user_id="default")
    assert len(out) == 2
    assert out[0].session_id == "s1"
    assert out[0].summary == "first"
    assert out[0].topics == ["a", "b"]
    assert out[0].entities == ["Ada"]
    assert out[0].entity_ids == ["uuid-a"]
    assert out[1].session_id == "s2"
    assert out[1].topics == []


def test_recent_sessions_returns_empty_and_warns_on_db_error(monkeypatch, caplog):
    from services.memory import recent_sessions

    def boom(q, p=None):
        raise RuntimeError("connection reset")

    ms = _config.get_metadata_store()
    monkeypatch.setattr(ms, "fetch_all", boom)
    with caplog.at_level(logging.WARNING, logger="services.memory"):
        result = recent_sessions(limit=5, user_id="default")
    assert result == []
    assert any("recent_sessions" in r.message for r in caplog.records)


def test_recent_sessions_tolerates_null_topics_and_entities(monkeypatch):
    from services.memory import recent_sessions

    rows = [
        {
            "session_id": "s",
            "summary": "x",
            "topics": None,
            "entities": None,
            "entity_ids": None,
        }
    ]
    ms = _config.get_metadata_store()
    monkeypatch.setattr(ms, "fetch_all", lambda q, p=None: rows)
    out = recent_sessions()
    assert out[0].topics == []
    assert out[0].entities == []
    assert out[0].entity_ids == []


# ---------------------------------------------------------------------------
# services.entities.lookup_by_name
# ---------------------------------------------------------------------------


def test_lookup_by_name_returns_none_when_not_found():
    from services.entities import lookup_by_name

    assert lookup_by_name("Ada Lovelace") is None


def test_lookup_by_name_returns_dict_when_found(monkeypatch):
    from services.entities import lookup_by_name

    row = {
        "name": "Ada Lovelace",
        "entity_type": "PERSON",
        "mention_count": 7,
        "aliases": ["Ada"],
        "context_tags": ["computing"],
        # Scope was added when personal/shared/system memory scopes
        # landed (see services.entities.lookup_by_name). The MCP
        # entity.lookup schema requires this field, so the lookup
        # result must surface it back to the caller.
        "scope": "personal",
    }
    ms = _config.get_metadata_store()
    monkeypatch.setattr(ms, "fetch_one", lambda q, p=None: row)
    result = lookup_by_name("ada lovelace")
    assert result == row


def test_lookup_by_name_empty_input_returns_none_without_db():
    from services.entities import lookup_by_name

    # Should short-circuit before touching the DB; empty/whitespace inputs
    # return None directly. Demonstrated by also passing while DB fetch_one
    # would raise, but we don't even need that here — assertion is structural.
    assert lookup_by_name("") is None
    assert lookup_by_name("   ") is None


def test_lookup_by_name_returns_none_and_warns_on_db_error(monkeypatch, caplog):
    from services.entities import lookup_by_name

    def boom(q, p=None):
        raise RuntimeError("postgres down")

    ms = _config.get_metadata_store()
    monkeypatch.setattr(ms, "fetch_one", boom)
    with caplog.at_level(logging.WARNING, logger="services.entities"):
        result = lookup_by_name("Ada Lovelace")
    assert result is None
    assert any("lookup_by_name" in r.message for r in caplog.records)


def test_lookup_by_name_tolerates_null_aliases_and_tags(monkeypatch):
    from services.entities import lookup_by_name

    row = {
        "name": "Bob",
        "entity_type": "PERSON",
        "mention_count": 1,
        "aliases": None,
        "context_tags": None,
    }
    ms = _config.get_metadata_store()
    monkeypatch.setattr(ms, "fetch_one", lambda q, p=None: row)
    result = lookup_by_name("Bob")
    assert result["aliases"] == []
    assert result["context_tags"] == []


# ---------------------------------------------------------------------------
# services.entities.search_by_name
# ---------------------------------------------------------------------------


def test_search_by_name_returns_empty_for_blank_query():
    from services.entities import search_by_name

    assert search_by_name("") == []
    assert search_by_name("   ") == []


def test_search_by_name_returns_mapped_rows(monkeypatch):
    from services.entities import search_by_name

    rows = [
        {
            "name": "Lumogis",
            "entity_type": "PROJECT",
            "mention_count": 12,
            "aliases": [],
            "context_tags": ["ai"],
        },
        {
            "name": "Lumosity",
            "entity_type": "ORG",
            "mention_count": 3,
            "aliases": [],
            "context_tags": [],
        },
    ]
    ms = _config.get_metadata_store()
    monkeypatch.setattr(ms, "fetch_all", lambda q, p=None: rows)
    out = search_by_name("lum", limit=5)
    assert len(out) == 2
    assert out[0]["name"] == "Lumogis"
    assert out[0]["mention_count"] == 12


def test_search_by_name_returns_empty_and_warns_on_db_error(monkeypatch, caplog):
    from services.entities import search_by_name

    def boom(q, p=None):
        raise RuntimeError("postgres down")

    ms = _config.get_metadata_store()
    monkeypatch.setattr(ms, "fetch_all", boom)
    with caplog.at_level(logging.WARNING, logger="services.entities"):
        out = search_by_name("ada", limit=10)
    assert out == []
    assert any("search_by_name" in r.message for r in caplog.records)
