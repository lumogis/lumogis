# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Batch handler: entity extraction from arbitrary text."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic import Field
from services.batch_queue import register_batch_handler
from services.entities import extract_entities
from services.entities import store_entities


class EntitiesExtractPayload(BaseModel):
    text: str
    evidence_id: str = Field(..., min_length=1, max_length=128)
    evidence_type: Literal["SESSION", "DOCUMENT"] = "SESSION"


@register_batch_handler("entities_extract", EntitiesExtractPayload)
def handle(*, user_id: str, payload: EntitiesExtractPayload) -> None:
    entities = extract_entities(payload.text, user_id=user_id)
    store_entities(
        entities,
        evidence_id=payload.evidence_id,
        evidence_type=payload.evidence_type,
        user_id=user_id,
    )
