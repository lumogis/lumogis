# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Coding-context entity/relation type registration (LUM-294).

Pure-model + service-spy tests: the new types are registered in the
`mcp_write` allowlists (consumed by the input-model validators), the tokens
honour the Cypher charset, the relation-extraction prompt auto-expands, and
`checkpoint` auto-creates a uniquely-named SESSION entity (degrade-safe).
"""

import re

import pytest
from models.mcp_write import ENTITY_TYPES
from models.mcp_write import RELATION_TYPES
from models.mcp_write import AddEntityInput
from models.mcp_write import AddRelationInput
from models.mcp_write import CheckpointResult

_CODING_ENTITY_TYPES = [
    "CODING_DECISION",
    "CODING_CONVENTION",
    "COMPONENT",
    "FAILURE",
    "SESSION",
    "TASK",
    "LIBRARY",
]
# The issue's 9 relation types (wire tokens — snake/upper, not CamelCase).
_ISSUE_RELATIONS = [
    "decided_by",
    "implements",
    "depends_on",
    "replaces",
    "supersedes",
    "caused_by",
    "discussed_in_session",
    "blocked_by",
    "references_issue",
]


# --- registration -----------------------------------------------------------


def test_entity_types_contains_coding_tokens():
    assert set(_CODING_ENTITY_TYPES) <= ENTITY_TYPES
    # base four retained (additive, no removal)
    assert {"PERSON", "ORG", "PROJECT", "CONCEPT"} <= ENTITY_TYPES


def test_relation_types_contains_issue_relations():
    assert {r.upper() for r in _ISSUE_RELATIONS} <= RELATION_TYPES
    # base relations retained
    assert {"DEPENDS_ON", "PART_OF", "DECIDED", "RELATES_TO", "SUPERSEDES"} <= RELATION_TYPES


def test_all_tokens_match_cypher_charset():
    # The graph projector interpolates relation_type into Cypher — ^[A-Z_]+$ is
    # the injection control. Entity types share the convention.
    for t in ENTITY_TYPES | RELATION_TYPES:
        assert re.fullmatch(r"[A-Z_]+", t), t


def test_registries_are_frozenset_no_migration():
    # Extensible in-code (no DB CHECK / no migration to add a type).
    assert isinstance(ENTITY_TYPES, frozenset)
    assert isinstance(RELATION_TYPES, frozenset)


# --- input-model validation -------------------------------------------------


@pytest.mark.parametrize("etype", _CODING_ENTITY_TYPES)
def test_add_entity_input_accepts_each_coding_type(etype):
    assert AddEntityInput(name="x", entity_type=etype).entity_type == etype


def test_add_entity_input_normalises_case():
    # The validator upper-cases (it does NOT CamelCase->snake): snake_case and
    # single-word any-case normalise; CamelCase multi-word does NOT.
    assert AddEntityInput(name="x", entity_type="coding_decision").entity_type == "CODING_DECISION"
    assert AddEntityInput(name="x", entity_type="component").entity_type == "COMPONENT"
    with pytest.raises(Exception):
        AddEntityInput(name="x", entity_type="CodingDecision")  # no underscore after upper()


def test_add_entity_input_rejects_unknown_type():
    with pytest.raises(Exception):
        AddEntityInput(name="x", entity_type="BOGUS")
    with pytest.raises(Exception):
        AddEntityInput(name="x", entity_type="")


@pytest.mark.parametrize("rel", _ISSUE_RELATIONS)
def test_add_relation_input_accepts_each_issue_relation(rel):
    out = AddRelationInput(src="a", dst="b", relation_type=rel)
    assert out.relation_type == rel.upper()


def test_add_relation_input_rejects_unknown():
    with pytest.raises(Exception):
        AddRelationInput(src="a", dst="b", relation_type="frobnicates")


def test_checkpoint_result_has_optional_entity_id():
    assert CheckpointResult(memory_id="m1").entity_id is None
    assert CheckpointResult(memory_id="m1", entity_id="e1").entity_id == "e1"


# --- relation-extraction prompt auto-expansion (the import-time coupling) ----


def test_relation_extraction_prompt_includes_new_tokens():
    from services import mcp_write

    prompt = mcp_write._EXTRACT_RELATIONS_PROMPT
    for tok in (
        "DECIDED_BY",
        "IMPLEMENTS",
        "CAUSED_BY",
        "DISCUSSED_IN_SESSION",
        "BLOCKED_BY",
        "REFERENCES_ISSUE",
        "REPLACES",
    ):
        assert tok in prompt, tok


# --- checkpoint -> SESSION entity -------------------------------------------


def test_checkpoint_creates_unique_session_entity(monkeypatch):
    from services import mcp_write

    monkeypatch.setattr(mcp_write.memories, "store_memory", lambda **k: "cp1")
    captured = {}
    monkeypatch.setattr(
        mcp_write,
        "store_entities",
        lambda entities, **k: captured.update(entity=entities[0], kwargs=k) or ["sess1"],
    )
    out = mcp_write.checkpoint(user_id="u", bank="coding", summary="chose FalkorDB over Neo4j")
    assert out["memory_id"] == "cp1"
    assert out["entity_id"] == "sess1"
    e = captured["entity"]
    assert e.entity_type == "SESSION"
    assert captured["kwargs"]["evidence_id"] == "cp1"
    assert captured["kwargs"]["skip_quality_gate"] is True
    assert "[cp1" in e.name or e.name.endswith("[cp1]") or "cp1"[:8] in e.name  # memory_id suffix


def test_checkpoint_degrades_when_session_write_fails(monkeypatch):
    from services import mcp_write

    monkeypatch.setattr(mcp_write.memories, "store_memory", lambda **k: "cp2")

    def _boom(*a, **k):
        raise RuntimeError("entity store down")

    monkeypatch.setattr(mcp_write, "store_entities", _boom)
    out = mcp_write.checkpoint(user_id="u", bank="coding", summary="x")
    assert out == {"memory_id": "cp2"}  # memory kept, no entity_id, no raise


def test_checkpoint_degrades_when_session_write_returns_empty(monkeypatch):
    # Second degrade branch (VERIFY-PLAN P3): store_entities returns [] (its own
    # internal error handling) → `if ids:` skipped, no entity_id, memory kept.
    from services import mcp_write

    monkeypatch.setattr(mcp_write.memories, "store_memory", lambda **k: "cp3")
    monkeypatch.setattr(mcp_write, "store_entities", lambda entities, **k: [])
    out = mcp_write.checkpoint(user_id="u", bank="coding", summary="x")
    assert out == {"memory_id": "cp3"}


def test_session_label_unique_and_bounded():
    from services.mcp_write import _session_label

    # same summary, different memory_id -> different names (no merge-by-name)
    a = _session_label("end of session", "aaaaaaaaaaaa")
    b = _session_label("end of session", "bbbbbbbbbbbb")
    assert a != b
    # empty summary -> non-empty fallback
    empty = _session_label("   ", "cccccccccccc")
    assert empty.startswith("Session") and empty.strip() == empty and len(empty) > 0
    # long summary bounded
    long_name = _session_label("y" * 5000, "dddddddddddd")
    assert len(long_name) <= 256
