# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Pydantic model for the MCP `recall` tool result (LUM-295).

`RecalledMemory` is the per-result contract returned by `services.recall.recall`
and the `recall` MCP tool. `entity_ids` is populated from the `entity_edges`
join in `services.recall._hydrate` (empty only when a memory genuinely has no
edges); `source_strategies` lists which retrieval legs surfaced the memory
(observability). Serialised with `model_dump(mode="json")` so datetimes become
ISO strings for JSON-RPC.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import Field


class RecalledMemory(BaseModel):
    id: str
    content: str
    entity_ids: list[str] = Field(default_factory=list)
    valid_from: datetime
    valid_until: datetime | None = None
    score: float
    source_strategies: list[str] = Field(default_factory=list)
