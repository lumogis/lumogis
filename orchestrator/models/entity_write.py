# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Entity summary OCC and guarded-write result models (LUM-358).

Canonical types for ``entity_write_guard`` and consolidation consumers
(LUM-108/109/106). See ``.cursor/plans/LUM-358-household-concurrent-write-isolation.plan.md``.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict

_REQ = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EntitySummarySnapshot(BaseModel):
    model_config = _REQ

    entity_id: UUID
    user_id: str
    scope: Literal["personal", "shared", "system"]
    entity_type: str
    summary: str = ""
    staged_summary: str | None = None
    version: int = 1


class GuardedWriteResult(BaseModel):
    model_config = _REQ

    ok: bool
    conflict: bool = False
    new_version: int | None = None


class StaleVersionError(Exception):
    """Optional exception flow for future callers; v1 helpers return GuardedWriteResult."""
