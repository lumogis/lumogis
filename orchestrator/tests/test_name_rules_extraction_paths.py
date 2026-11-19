# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Where a deleted name must not come back, and what happens when the rules can't be read (LUM-966).

Three extraction paths read the member's name rules: document ingest, the MCP
``add_memory`` tool (entities and LLM-chosen relation endpoints), and the batch
handlers. ``apply_name_rules`` fails closed, so each path has a defined outcome
when the rules read fails. The real ``apply_name_rules`` runs here; only the
metadata store is a fake.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from models.entities import ExtractedEntity
from models.mcp_write import ExtractedRelation

import config as _config
from services import entity_name_rules as enr
from services import ingest
from services import mcp_write

USER = "alice"


class RulesStore:
    """Answers the rules read from ``rules``; everything else finds nothing."""

    def __init__(self, rules=None, fail_rules=False):
        self.rules = rules or []
        self.fail_rules = fail_rules
        self.executed: list[tuple[str, tuple]] = []

    def fetch_all(self, sql, params=None):
        if "FROM entity_name_rules" in sql:
            if self.fail_rules:
                raise RuntimeError("relation entity_name_rules does not exist")
            return list(self.rules)
        return []

    def fetch_one(self, sql, params=None):
        return None

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), tuple(params or ())))


@pytest.fixture
def store():
    ms = RulesStore()
    _config._instances["metadata_store"] = ms
    yield ms
    _config._instances.clear()


def _block(name_norm, kind):
    return {"name_norm": name_norm, "from_type": kind, "action": "block", "to_type": None}


# ── U14: document ingest ─────────────────────────────────────────────────────


def test_ingest_clears_the_file_hash_when_name_rules_are_unavailable(store, caplog):
    with (
        patch("services.ingest.hooks.fire_background"),
        patch(
            "services.entities.extract_entities",
            return_value=[ExtractedEntity(name="Total", entity_type="PERSON")],
        ),
        patch("services.entities.store_entities", side_effect=enr.NameRulesUnavailable("x")),
        caplog.at_level(logging.WARNING, logger="services.ingest"),
    ):
        out = ingest._emit_document_ingested_and_entities(
            logical_path="/data/a.pdf", chunk_count_written=1, user_id=USER, text="Total"
        )
    assert out == []
    (sql, params) = store.executed[0]
    assert sql.startswith("UPDATE file_index SET file_hash = ''")
    assert "published_from IS NULL" in sql
    assert "updated_at = to_timestamp(0)" in sql  # so the poll-mode mtime fast path re-reads
    assert params == (USER, "/data/a.pdf")
    assert "a.pdf" not in caplog.text and "Total" not in caplog.text


def test_ingest_does_not_clear_the_hash_on_a_generic_failure(store):
    with (
        patch("services.ingest.hooks.fire_background"),
        patch("services.entities.extract_entities", side_effect=RuntimeError("llm down")),
    ):
        out = ingest._emit_document_ingested_and_entities(
            logical_path="/data/a.pdf", chunk_count_written=1, user_id=USER, text="x"
        )
    assert out == []
    assert store.executed == []


# ── U16–U18: MCP add_memory / add_relation ───────────────────────────────────


@pytest.fixture
def mcp(monkeypatch):
    monkeypatch.setattr(mcp_write.memories, "store_memory", lambda **k: "mem1")
    monkeypatch.setattr(mcp_write, "extract_entities", lambda *a, **k: [])
    stored: list[tuple[str, str]] = []

    def fake_store(entities, evidence_id, evidence_type, user_id, *, skip_quality_gate=False):
        stored.extend((e.name, e.entity_type) for e in entities)
        return [f"id-{e.name}" for e in entities]

    monkeypatch.setattr(mcp_write, "store_entities", fake_store)
    monkeypatch.setattr(mcp_write.entity_edges, "store_edge", lambda **k: "edge1")
    return stored


def _rel(src, dst):
    return ExtractedRelation(src_name=src, dst_name=dst, relation_type="DEPENDS_ON")


def test_add_memory_skips_a_relation_whose_endpoint_is_a_blocked_name(mcp, monkeypatch, caplog):
    """U16: the most likely re-learn path for a deleted CONCEPT entry."""
    _config._instances["metadata_store"] = RulesStore([_block("total", "CONCEPT")])
    try:
        monkeypatch.setattr(
            mcp_write,
            "extract_relations",
            lambda *a, **k: [_rel("Total", "Budget"), _rel("Budget", "Plan")],
        )
        with caplog.at_level(logging.WARNING, logger="services.mcp_write"):
            out = mcp_write.add_memory(user_id=USER, bank="b", content="Total / Budget / Plan")
    finally:
        _config._instances.clear()
    assert out == {"memory_id": "mem1", "entity_ids": [], "relation_ids": ["edge1"]}
    assert ("Total", "CONCEPT") not in mcp  # never stored
    assert ("Budget", "CONCEPT") in mcp
    assert "skipping a DEPENDS_ON relation" in caplog.text
    assert "Total" not in caplog.text  # a name is user content


def test_add_memory_stores_a_retyped_endpoint_at_the_corrected_kind(mcp, monkeypatch):
    retype = {"name_norm": "total", "from_type": "CONCEPT", "action": "retype", "to_type": "ORG"}
    _config._instances["metadata_store"] = RulesStore([retype])
    try:
        monkeypatch.setattr(mcp_write, "extract_relations", lambda *a, **k: [_rel("Total", "Plan")])
        mcp_write.add_memory(user_id=USER, bank="b", content="x")
    finally:
        _config._instances.clear()
    assert ("Total", "ORG") in mcp


def test_add_relation_is_explicit_and_wins_over_a_block(mcp):
    """U17 (residual 3): the explicit tool is never blocked."""
    _config._instances["metadata_store"] = RulesStore([_block("total", "CONCEPT")])
    try:
        out = mcp_write.add_relation(
            user_id=USER, bank="b", src="Total", dst="Plan", relation_type="DEPENDS_ON"
        )
    finally:
        _config._instances.clear()
    assert out == {"relation_id": "edge1"}
    assert ("Total", "CONCEPT") in mcp


def test_add_memory_keeps_the_memory_when_name_rules_are_unavailable(monkeypatch, caplog):
    """U18: no error after a stored memory, so a client retry cannot duplicate it."""
    monkeypatch.setattr(mcp_write.memories, "store_memory", lambda **k: "mem1")
    monkeypatch.setattr(
        mcp_write,
        "extract_entities",
        lambda *a, **k: [ExtractedEntity(name="A", entity_type="CONCEPT")],
    )
    monkeypatch.setattr(
        mcp_write, "extract_relations", lambda *a, **k: pytest.fail("relations must be skipped")
    )
    _config._instances["metadata_store"] = RulesStore(fail_rules=True)
    try:
        with caplog.at_level(logging.WARNING, logger="services.mcp_write"):
            out = mcp_write.add_memory(user_id=USER, bank="b", content="x")
    finally:
        _config._instances.clear()
    assert out == {"memory_id": "mem1", "entity_ids": [], "relation_ids": []}
    assert "entities deferred: name rules unavailable" in caplog.text


# ── store_entities: no name in the failure log ───────────────────────────────


def test_store_entities_failure_log_carries_no_name(caplog):
    from services import entities

    with (
        patch.object(entities.entity_name_rules, "apply_name_rules", side_effect=lambda e, u: e),
        patch.object(
            entities.entity_quality, "score_and_filter_entities", side_effect=lambda e, u: (e, 0)
        ),
        patch.object(entities.config, "get_metadata_store", return_value=MagicMock()),
        patch.object(entities.config, "get_embedder", return_value=MagicMock()),
        patch.object(entities.config, "get_vector_store", return_value=MagicMock()),
        patch.object(entities, "_upsert_entity", side_effect=ValueError("Secret Name broke")),
        patch.object(entities.entity_constraints, "run_batch_constraints"),
        caplog.at_level(logging.ERROR, logger="services.entities"),
    ):
        ids = entities.store_entities(
            [ExtractedEntity(name="Secret Name", entity_type="PERSON")], "ev", "DOCUMENT", USER
        )
    assert ids == []
    assert "Failed to store entity" in caplog.text
    assert "Secret Name" not in caplog.text
