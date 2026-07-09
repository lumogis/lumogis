# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Context injection result types for chat (LUM-175 document chat)."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field


@dataclass(frozen=True)
class DocumentCitation:
    chunk_index: int | None
    file_path: str
    score: float
    score_kind: str


@dataclass
class ContextInjectionResult:
    messages: list[dict]
    citations: list[DocumentCitation] = field(default_factory=list)
    auto_rag_point_ids: set[str] = field(default_factory=set)
