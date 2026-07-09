# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Biography conflict detection and resolution models (LUM-514).

Machine-readable contracts for LUM-515 (biography_pins), LUM-516 (synthesis),
and LUM-518 (review UI). See also ``docs/private/specs/biography-conflict-acceptance.md``.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

_REQ = ConfigDict(extra="forbid", str_strip_whitespace=True)
_RES = ConfigDict(extra="ignore")


class ConflictStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class ResolutionAction(str, Enum):
    CONFIRM_ONE = "confirm_one"
    KEEP_BOTH = "keep_both"
    DISMISS = "dismiss"


class ConflictEligibleCategory(str, Enum):
    PREFERENCE = "preference"
    RELATIONSHIP = "relationship"
    FOCUS = "focus"
    LOGISTICS = "logistics"
    OTHER = "other"


BiographyCategory = Literal[
    "identity",
    "preference",
    "relationship",
    "focus",
    "logistics",
    "other",
]
BiographyScope = Literal["personal", "shared"]


class ConflictContribution(BaseModel):
    model_config = _RES

    user_id: str
    pin_id: UUID
    text: str
    updated_at: datetime


class BiographyPinSnapshot(BaseModel):
    model_config = _RES

    id: UUID
    user_id: str
    text: str
    category: BiographyCategory
    domain: str | None = None
    scope: BiographyScope
    subject_entity_id: UUID | None = None
    subject_text: str | None = None
    published_from: UUID | None = None
    updated_at: datetime


class DetectedConflict(BaseModel):
    model_config = _RES

    fact_group_key: str
    category: str
    domain: str | None
    pin_ids: list[UUID]
    contributions: list[ConflictContribution]
    requires_review: bool
    represent_both_line: str


class ConflictResolutionRequest(BaseModel):
    model_config = _REQ

    action: Literal["confirm_one", "keep_both", "dismiss"]
    chosen_pin_id: UUID | None = None
    context_note: str | None = Field(None, max_length=500)


class ConflictResolution(BaseModel):
    model_config = _RES

    id: UUID
    fact_group_key: str
    status: Literal["open", "resolved", "dismissed"]
    action: str | None = None
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    archived_pin_ids: list[UUID] = Field(default_factory=list)


class BiographyConflictListResponse(BaseModel):
    model_config = _RES

    conflicts: list[ConflictResolution]
