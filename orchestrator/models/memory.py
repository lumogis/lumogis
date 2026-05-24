# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
from typing import Literal

from pydantic import BaseModel


class SessionSummary(BaseModel):
    session_id: str
    summary: str
    topics: list[str] = []
    entities: list[str] = []
    entity_ids: list[str] = []
    scope: str = "personal"


class ContextHit(BaseModel):
    session_id: str
    summary: str
    score: float
    scope: str = "personal"


class DocumentContextHit(BaseModel):
    """One document chunk candidate for chat auto-RAG (LUM-308)."""

    point_id: str
    file_path: str
    chunk_text: str
    score: float
    score_kind: Literal["rerank", "bi_encoder", "rrf_gated"]
    rerank_score: float | None = None
    scope: str = "personal"
    ingested: str | None = None
    metadata: dict = {}
    chunk_index: int | None = None
