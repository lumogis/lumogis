# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
from pydantic import BaseModel


class SessionSummary(BaseModel):
    session_id: str
    summary: str
    topics: list[str] = []
    entities: list[str] = []
    entity_ids: list[str] = []


class ContextHit(BaseModel):
    session_id: str
    summary: str
    score: float
