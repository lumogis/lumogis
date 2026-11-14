# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Wire models for the user-editable email taxonomy + classification rules (LUM-629).

All models forbid extra fields so v1 rejects unknown match conditions and
mistyped payloads at the edge. ``body_contains`` (LUM-630) is a supported
condition, evaluated on the extracted plain-text body of full-ingested mail.
``is_custom`` / ``is_protected`` are response-only, server-derived (never
stored, never client-supplied).
"""

from __future__ import annotations

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class CategoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    label: str
    enabled: bool
    is_custom: bool
    is_protected: bool
    sort: int


class TaxonomyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categories: list[CategoryItem]


class CategoryPatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    # Partial-patch semantics: an omitted/None field keeps the current stored
    # value (no field-level reset-to-default in v1).
    label: str | None = None
    enabled: bool | None = None
    sort: int | None = None


class TaxonomyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categories: list[CategoryPatchItem] = Field(default_factory=list)
    deletes: list[str] = Field(default_factory=list)  # custom slugs only


class RuleMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sender: list[str] = Field(default_factory=list)
    sender_domain: list[str] = Field(default_factory=list)
    subject_contains: list[str] = Field(default_factory=list)
    # LUM-630: matched on the extracted plain-text body — only present for
    # full-ingested mail, so a body-only rule is inert on metadata-only mail and
    # at reclassify (body not persisted).
    body_contains: list[str] = Field(default_factory=list)
    # False = unconstrained; True = require a bulk header (never fires at reclassify).
    bulk_header: bool = False


class RuleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    priority: int = 100
    match: RuleMatch = Field(default_factory=RuleMatch)
    category: str
    enabled: bool = True
    # Response-only: set when this rule's category is not currently enabled so
    # the UI can flag a dead rule. Ignored on input.
    category_disabled: bool = False


class RulesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[RuleItem]


class RulesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[RuleItem] = Field(default_factory=list)


class ReclassifyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    scanned: int
    moved: int
    failed: int
