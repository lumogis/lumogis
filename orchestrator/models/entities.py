# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
from pydantic import BaseModel


class ExtractedEntity(BaseModel):
    name: str
    entity_type: str
    aliases: list[str] = []
    context_tags: list[str] = []


class EntityRelation(BaseModel):
    source_name: str
    relation_type: str
    evidence_type: str
    evidence_id: str
