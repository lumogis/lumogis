# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Pydantic models + dataclasses for the per-user export / import surface.

Owned by ``services/user_export.py`` (see plan
``per_user_backup_export``); routes in ``routes/me.py`` and
``routes/admin_users.py`` consume these and convert to/from JSON at
the wire boundary.

Three groups:

* Request / response models (Pydantic) used directly on the wire:
  :class:`ExportRequest`, :class:`NewUserSpec`, :class:`ImportRequest`,
  :class:`ImportPlan`, :class:`ImportPreconditions`, :class:`ImportReceipt`,
  :class:`PruneReceipt`, :class:`ArchiveInventoryEntry`,
  :class:`SectionSummary`, :class:`DanglingReference`.

* Internal dataclasses for archive metadata: :class:`Manifest`,
  :class:`ArchiveEntry`, :class:`ArchiveMeta`, :class:`PruneDecision`.

* Import refusal exception: :class:`ImportRefused` carries the structured
  ``refusal_reason`` so route handlers can map it to 400/403/409/413
  without re-deriving the reason from a free-form message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field

from models.auth import Role


# ─── Request models ─────────────────────────────────────────────────────────


class ExportRequest(BaseModel):
    """Body for ``POST /api/v1/me/export``. Optional — omit for self-export."""

    target_user_id: str | None = Field(
        default=None,
        description=(
            "Admin-only override. Non-admins setting this to a non-self id "
            "→ 403. Mirrors the routes/data.py:/ingest precedent (body field, "
            "not query param)."
        ),
    )


class NewUserSpec(BaseModel):
    """Identity for the freshly-minted user on the destination instance.

    The 12-character password floor lives HERE (Pydantic 422); the
    underlying ``services/users.py:create_user`` only requires non-empty.
    """

    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    role: Role = "user"


class ImportRequest(BaseModel):
    """Body for ``POST /api/v1/admin/user-imports``."""

    archive_path: str = Field(
        description="Path under USER_EXPORT_DIR allowlist (validated by the service)."
    )
    new_user: NewUserSpec
    dry_run: bool = False


# ─── Response models ────────────────────────────────────────────────────────


class SectionSummary(BaseModel):
    name: str
    kind: Literal[
        "postgres",
        "qdrant",
        "falkordb",
        "user_record",
        "capture_media",
    ]
    row_count: int


class DanglingReference(BaseModel):
    section: str
    field: str
    count: int
    sample_values: list[str] = Field(default_factory=list, max_length=5)


class ImportPreconditions(BaseModel):
    archive_integrity_ok: bool
    manifest_present: bool
    manifest_parses: bool
    manifest_version_supported: bool
    target_email_available: bool
    all_required_sections_present: bool
    no_parent_pk_collisions: bool


class ImportPlan(BaseModel):
    manifest_version: int
    scope_filter: str
    falkordb_edge_policy: str
    exported_user: dict
    sections: list[SectionSummary]
    missing_sections: list[str]
    dangling_references: list[DanglingReference]
    falkordb_external_edge_count: int
    preconditions: ImportPreconditions
    would_succeed: bool
    warnings: list[str]


class ImportReceipt(BaseModel):
    new_user_id: str
    archive_filename: str
    sections_imported: list[SectionSummary]
    qdrant_zero_vector_count: int = 0
    falkordb_nodes_imported: int = 0
    falkordb_edges_imported: int = 0
    falkordb_external_edges_skipped: int = 0
    leaf_pk_collisions_per_table: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class PruneReceipt(BaseModel):
    user_id: str
    archives_kept: int
    archives_pruned: int
    pruned_filenames: list[str]
    policy: dict


class ArchiveInventoryEntry(BaseModel):
    user_id: str
    archive_filename: str
    bytes: int
    mtime: datetime
    manifest_status: Literal[
        "valid", "unparseable", "missing_manifest", "unsupported_version"
    ]
    manifest_version: int | None = None
    exported_user_email: str | None = None


# ─── Internal dataclasses (not API-facing) ──────────────────────────────────


@dataclass
class Manifest:
    """Parsed contents of ``manifest.json`` inside an archive."""

    format_version: int
    exported_at: datetime
    exporting_user_id: str
    exported_user_email: str
    exported_user_role: str
    scope_filter: str  # always "authored_by_me" in v1
    falkordb_edge_policy: str  # always "personal_intra_user_authored" in v1
    sections: list[dict]  # [{name, kind, row_count}]
    falkordb_external_edge_count: int = 0


@dataclass
class ArchiveEntry:
    name: str
    size_bytes: int


@dataclass
class ArchiveMeta:
    path: Path
    mtime: datetime


@dataclass
class PruneDecision:
    keep: list[Path] = field(default_factory=list)
    prune: list[Path] = field(default_factory=list)
    reason_per_path: dict[Path, str] = field(default_factory=dict)


# ─── Exceptions ─────────────────────────────────────────────────────────────


class ImportRefused(Exception):
    """Raised by ``services.user_export`` when an import cannot proceed.

    Carries the structured ``refusal_reason`` so the route handler can
    map it cleanly to an HTTP status + audit row without re-deriving
    intent from a free-form string.

    ``payload`` is an optional structured detail (e.g. the
    ``collisions`` list for ``uuid_collision_on_parent_table``) included
    in the HTTP response body and the ``__user_import__.refused`` audit
    row's ``result_summary``.
    """

    def __init__(self, refusal_reason: str, payload: Any | None = None) -> None:
        super().__init__(refusal_reason)
        self.refusal_reason = refusal_reason
        self.payload = payload


__all__ = [
    "ExportRequest",
    "NewUserSpec",
    "ImportRequest",
    "SectionSummary",
    "DanglingReference",
    "ImportPreconditions",
    "ImportPlan",
    "ImportReceipt",
    "PruneReceipt",
    "ArchiveInventoryEntry",
    "Manifest",
    "ArchiveEntry",
    "ArchiveMeta",
    "PruneDecision",
    "ImportRefused",
]
