# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
from pydantic import BaseModel


class MergeResult(BaseModel):
    winner_id: str
    loser_id: str
    aliases_merged: int
    relations_moved: int
    sessions_updated: int
    qdrant_cleaned: bool


class ExtractedEntity(BaseModel):
    name: str
    entity_type: str
    aliases: list[str] = []
    context_tags: list[str] = []
    # Set in-memory by entity_quality.score_and_filter_entities() before persistence.
    # None means the scorer was not run (pre-Pass-1 code path or fail-open exception).
    extraction_quality: float | None = None
    is_staged: bool | None = None


class EntityRelation(BaseModel):
    source_name: str
    relation_type: str
    evidence_type: str
    evidence_id: str
