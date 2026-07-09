# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Data contracts for the MCP memory write surface (LUM-291).

Tool input/result models plus the two allowlists that are *load-bearing*:

* ``RELATION_TYPES`` — UPPERCASE edge-type tokens. The graph projector
  interpolates the relation type into a Cypher ``MERGE (a)-[:<TYPE>]->(b)``;
  Cypher cannot bind a relationship type as a parameter, so this allowlist
  (each matching ``^[A-Z_]+$``) is the injection control, enforced before any
  Cypher runs.
* ``ENTITY_TYPES`` — the allowlist ``add_entity`` validates against. The base
  four (``PERSON``/``ORG``/``PROJECT``/``CONCEPT``) are the keys of
  ``NodeLabel.ENTITY_TYPE_MAP`` in the graph schema; the coding-context types
  (LUM-294: ``CODING_DECISION``/``CODING_CONVENTION``/``COMPONENT``/``FAILURE``/
  ``SESSION``/``TASK``/``LIBRARY``) are registered here on the Postgres/MCP side.
  The graph ``ENTITY_TYPE_MAP`` sync for the coding types is a deferred
  follow-up (graph default-off; ``for_entity_type`` degrades unknown types to
  ``Concept`` gracefully), so the "ENTITY_TYPES == ENTITY_TYPE_MAP keys"
  invariant is temporarily one-sided.
"""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel
from pydantic import Field
from pydantic import field_validator

# UPPERCASE — matches the existing EdgeType convention (MENTIONS / RELATES_TO)
# and the ^[A-Z_]+$ Cypher charset. See module docstring.
# Extending this set auto-expands services/mcp_write.py::_EXTRACT_RELATIONS_PROMPT
# (built at import from sorted(RELATION_TYPES)) — i.e. add_memory's LLM relation
# extraction will propose the coding relations too (LUM-294).
RELATION_TYPES: frozenset[str] = frozenset(
    {
        # base (LUM-291)
        "DEPENDS_ON", "PART_OF", "DECIDED", "RELATES_TO", "SUPERSEDES",
        # coding-context (LUM-294). DECIDED_BY (subject->decision) coexists with
        # the base DECIDED (decision->subject); both retained, additive.
        "DECIDED_BY", "IMPLEMENTS", "REPLACES", "CAUSED_BY",
        "DISCUSSED_IN_SESSION", "BLOCKED_BY", "REFERENCES_ISSUE",
        # code-structure (LUM-301). CALLS is the only net-new token; like the
        # others it auto-expands add_memory's _EXTRACT_RELATIONS_PROMPT (built
        # from sorted(RELATION_TYPES)) — intended, benign, still graph-gated.
        "CALLS",
    }
)

# add_entity allowlist. Base four are also keys of NodeLabel.ENTITY_TYPE_MAP;
# the coding types (LUM-294) are Postgres/MCP-side (graph map sync deferred).
ENTITY_TYPES: frozenset[str] = frozenset(
    {
        "PERSON", "ORG", "PROJECT", "CONCEPT",
        "CODING_DECISION", "CODING_CONVENTION", "COMPONENT", "FAILURE",
        "SESSION", "TASK", "LIBRARY",
    }
)

_MAX_CONTENT = 8000
_MAX_NAME = 256
_MAX_METADATA_BYTES = 4096
_MAX_LIST_ITEMS = 32
_MAX_TAG_LEN = 64


class ExtractedRelation(BaseModel):
    """A typed relation proposed by ``extract_relations`` (LLM) — endpoints by name."""

    src_name: str
    dst_name: str
    relation_type: str

    @field_validator("relation_type")
    @classmethod
    def _rel_allowed(cls, v: str) -> str:
        up = v.strip().upper()
        if up not in RELATION_TYPES:
            raise ValueError(f"relation_type {v!r} not in {sorted(RELATION_TYPES)}")
        return up


class MemoryRow(BaseModel):
    """Return shape of ``memories.get_memory``."""

    id: str
    user_id: str
    bank: str
    content: str
    tags: list[str]
    metadata: dict
    valid_from: datetime
    valid_until: datetime | None
    created_at: datetime


class AddMemoryResult(BaseModel):
    memory_id: str
    entity_ids: list[str]
    relation_ids: list[str]


class AddEntityResult(BaseModel):
    entity_id: str


class AddRelationResult(BaseModel):
    relation_id: str


def _validate_bank(v: str) -> str:
    from services import banks

    return banks.validate_bank_for_write(v)


class AddMemoryInput(BaseModel):
    content: str = Field(min_length=1, max_length=_MAX_CONTENT)
    bank: str = "coding"
    tags: list[str] | None = None
    metadata: dict | None = None

    @field_validator("bank")
    @classmethod
    def _bank(cls, v: str) -> str:
        return _validate_bank(v)

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if len(v) > _MAX_LIST_ITEMS or any(len(t) > _MAX_TAG_LEN for t in v):
            raise ValueError(f"≤{_MAX_LIST_ITEMS} tags, each ≤{_MAX_TAG_LEN} chars")
        return v

    @field_validator("metadata")
    @classmethod
    def _metadata(cls, v: dict | None) -> dict | None:
        if v is not None and len(json.dumps(v)) > _MAX_METADATA_BYTES:
            raise ValueError(f"metadata serialised JSON must be ≤{_MAX_METADATA_BYTES} bytes")
        return v


class AddEntityInput(BaseModel):
    name: str = Field(min_length=1, max_length=_MAX_NAME)
    entity_type: str
    bank: str = "coding"
    aliases: list[str] | None = None
    context_tags: list[str] | None = None

    @field_validator("entity_type")
    @classmethod
    def _etype(cls, v: str) -> str:
        up = v.strip().upper()
        if up not in ENTITY_TYPES:
            raise ValueError(f"entity_type {v!r} not in {sorted(ENTITY_TYPES)}")
        return up

    @field_validator("bank")
    @classmethod
    def _bank(cls, v: str) -> str:
        return _validate_bank(v)


class AddRelationInput(BaseModel):
    src: str = Field(min_length=1, max_length=_MAX_NAME)
    dst: str = Field(min_length=1, max_length=_MAX_NAME)
    relation_type: str
    bank: str = "coding"

    @field_validator("relation_type")
    @classmethod
    def _rel(cls, v: str) -> str:
        up = v.strip().upper()
        if up not in RELATION_TYPES:
            raise ValueError(f"relation_type {v!r} not in {sorted(RELATION_TYPES)}")
        return up

    @field_validator("bank")
    @classmethod
    def _bank(cls, v: str) -> str:
        return _validate_bank(v)


# --- LUM-526: supersede / archive tools --------------------------------------

class ForgetInput(BaseModel):
    memory_id: str = Field(min_length=1, max_length=64)


class ForgetResult(BaseModel):
    memory_id: str
    archived: bool


class UpdateObservationInput(BaseModel):
    memory_id: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=_MAX_CONTENT)
    tags: list[str] | None = None
    metadata: dict | None = None

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if len(v) > _MAX_LIST_ITEMS or any(len(t) > _MAX_TAG_LEN for t in v):
            raise ValueError(f"≤{_MAX_LIST_ITEMS} tags, each ≤{_MAX_TAG_LEN} chars")
        return v

    @field_validator("metadata")
    @classmethod
    def _metadata(cls, v: dict | None) -> dict | None:
        if v is not None and len(json.dumps(v)) > _MAX_METADATA_BYTES:
            raise ValueError(f"metadata serialised JSON must be ≤{_MAX_METADATA_BYTES} bytes")
        return v


class UpdateObservationResult(BaseModel):
    old_memory_id: str
    new_memory_id: str
    entity_ids: list[str]
    relation_ids: list[str]


class CheckpointInput(BaseModel):
    summary: str = Field(min_length=1, max_length=_MAX_CONTENT)
    bank: str = "coding"

    @field_validator("bank")
    @classmethod
    def _bank(cls, v: str) -> str:
        return _validate_bank(v)


class CheckpointResult(BaseModel):
    memory_id: str
    entity_id: str | None = None  # the auto-created SESSION entity (LUM-294); None if that write degraded
