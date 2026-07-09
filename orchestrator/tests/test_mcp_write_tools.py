# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for the MCP write surface orchestrator (LUM-291)."""

from unittest.mock import Mock

import pytest
from models.entities import ExtractedEntity
from models.mcp_write import AddEntityInput
from models.mcp_write import AddMemoryInput
from models.mcp_write import ExtractedRelation
from services import mcp_write


def test_add_memory_degrades_when_no_extraction(monkeypatch):
    monkeypatch.setattr(mcp_write.memories, "store_memory", lambda **k: "mem1")
    monkeypatch.setattr(mcp_write, "extract_entities", lambda *a, **k: [])
    monkeypatch.setattr(mcp_write, "extract_relations", lambda *a, **k: [])
    out = mcp_write.add_memory(user_id="u", bank="coding", content="hi")
    assert out == {"memory_id": "mem1", "entity_ids": [], "relation_ids": []}


def test_add_memory_stores_entities_and_edges(monkeypatch):
    monkeypatch.setattr(mcp_write.memories, "store_memory", lambda **k: "mem1")
    monkeypatch.setattr(
        mcp_write, "extract_entities",
        lambda *a, **k: [ExtractedEntity(name="A", entity_type="CONCEPT"),
                         ExtractedEntity(name="B", entity_type="CONCEPT")],
    )
    monkeypatch.setattr(mcp_write, "store_entities", lambda *a, **k: ["a", "b"])
    monkeypatch.setattr(
        mcp_write, "extract_relations",
        lambda *a, **k: [ExtractedRelation(src_name="A", dst_name="B", relation_type="DEPENDS_ON")],
    )
    monkeypatch.setattr(mcp_write, "_resolve_or_create_entity", lambda name, **k: f"id-{name}")
    monkeypatch.setattr(mcp_write.entity_edges, "store_edge", lambda **k: "edge1")
    out = mcp_write.add_memory(user_id="u", bank="coding", content="A depends on B")
    assert out["memory_id"] == "mem1"
    assert out["entity_ids"] == ["a", "b"]
    assert out["relation_ids"] == ["edge1"]


def test_add_entity_bypasses_quality_gate(monkeypatch):
    captured = {}

    def fake_store(entities, evidence_id, evidence_type, user_id, *, skip_quality_gate=False):
        captured["skip"] = skip_quality_gate
        captured["evidence_type"] = evidence_type
        return ["ent1"]

    monkeypatch.setattr(mcp_write, "store_entities", fake_store)
    out = mcp_write.add_entity(user_id="u", bank="coding", name="X", entity_type="CONCEPT")
    assert out == {"entity_id": "ent1"}
    assert captured["skip"] is True
    assert captured["evidence_type"] == "MEMORY"


def test_add_relation_resolves_and_stores(monkeypatch):
    monkeypatch.setattr(mcp_write, "_resolve_or_create_entity", lambda name, **k: f"id-{name}")
    captured = {}
    monkeypatch.setattr(
        mcp_write.entity_edges, "store_edge",
        lambda **k: captured.update(k) or "edge1",
    )
    out = mcp_write.add_relation(user_id="u", bank="coding", src="A", dst="B", relation_type="DEPENDS_ON")
    assert out == {"relation_id": "edge1"}
    assert captured["src_entity_id"] == "id-A" and captured["dst_entity_id"] == "id-B"


def test_memory_evidence_maps_to_mentioned_in_memory(monkeypatch):
    """Proves the entities.py change: evidence_type='MEMORY' → MENTIONED_IN_MEMORY."""
    import config as cfg
    from services import entities

    ms = Mock()
    ms.fetch_one.return_value = None  # no existing entity → fresh insert
    emb = Mock()
    emb.embed.return_value = [0.1] * 4
    vs = Mock()
    monkeypatch.setattr(cfg, "get_metadata_store", lambda: ms)
    monkeypatch.setattr(cfg, "get_embedder", lambda: emb)
    monkeypatch.setattr(cfg, "get_vector_store", lambda: vs)
    monkeypatch.setattr(entities.entity_constraints, "run_batch_constraints", lambda *a, **k: 0)
    monkeypatch.setattr(entities.hooks, "fire_background", lambda *a, **k: None)

    ids = entities.store_entities(
        [ExtractedEntity(name="FalkorDB", entity_type="PROJECT")],
        evidence_id="m1", evidence_type="MEMORY", skip_quality_gate=True,
    )
    assert len(ids) == 1
    rel_inserts = [c for c in ms.execute.call_args_list if "entity_relations" in c.args[0]]
    assert rel_inserts
    assert any("MENTIONED_IN_MEMORY" in str(c.args[1]) for c in rel_inserts)


def test_explicit_entity_with_low_quality_name_not_discarded(monkeypatch):
    """skip_quality_gate routes around score_and_filter_entities (which would
    otherwise discard a stop-list / low-score name)."""
    import config as cfg
    from services import entities

    ms = Mock()
    ms.fetch_one.return_value = None
    emb = Mock()
    emb.embed.return_value = [0.1]
    vs = Mock()
    monkeypatch.setattr(cfg, "get_metadata_store", lambda: ms)
    monkeypatch.setattr(cfg, "get_embedder", lambda: emb)
    monkeypatch.setattr(cfg, "get_vector_store", lambda: vs)
    monkeypatch.setattr(entities.entity_constraints, "run_batch_constraints", lambda *a, **k: 0)
    monkeypatch.setattr(entities.hooks, "fire_background", lambda *a, **k: None)

    # "the" would normally be discarded by the quality gate's stop-list.
    ids = entities.store_entities(
        [ExtractedEntity(name="the", entity_type="CONCEPT")],
        evidence_id="e1", evidence_type="MEMORY", skip_quality_gate=True,
    )
    assert len(ids) == 1  # written despite the low-quality name


def test_input_validation_bounds_and_allowlists():
    # entity_type ORG maps; ORGANISATION rejected (would fall to Concept in graph)
    assert AddEntityInput(name="x", entity_type="org").entity_type == "ORG"
    with pytest.raises(Exception):
        AddEntityInput(name="x", entity_type="ORGANISATION")
    # content cap
    with pytest.raises(Exception):
        AddMemoryInput(content="x" * 8001)
    # bank charset
    with pytest.raises(Exception):
        AddMemoryInput(content="ok", bank="Bad Bank!")


def test_add_memory_extracted_entities_still_gated(monkeypatch):
    """The asymmetry guard: add_memory must NOT pass skip_quality_gate for
    its LLM-extracted entities (a blanket bypass would admit hallucinations)."""
    from models.entities import ExtractedEntity

    captured = {}

    def spy_store(entities, evidence_id, evidence_type, user_id, *, skip_quality_gate=False):
        captured["skip"] = skip_quality_gate
        return []

    monkeypatch.setattr(mcp_write.memories, "store_memory", lambda **k: "mem1")
    monkeypatch.setattr(
        mcp_write, "extract_entities",
        lambda *a, **k: [ExtractedEntity(name="A", entity_type="CONCEPT")],
    )
    monkeypatch.setattr(mcp_write, "store_entities", spy_store)
    monkeypatch.setattr(mcp_write, "extract_relations", lambda *a, **k: [])
    mcp_write.add_memory(user_id="u", bank="coding", content="hi")
    assert captured["skip"] is False  # gate stays ON for extracted entities


def test_add_relation_input_rejects_off_allowlist():
    from models.mcp_write import AddRelationInput

    with pytest.raises(Exception):
        AddRelationInput(src="A", dst="B", relation_type="DROP TABLE")
