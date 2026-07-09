# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Canonical registry of household-shareable resource types (LUM-583 / LUM-584).

One source of truth for the six scope-bearing types that can be shared with
the household via the personal/shared/system projection model (ADR-015). Keyed
by the **public route segment** (matching ``routes/scope.py``'s publish/unpublish
routes), so a ``resource_type`` value doubles as the path segment for
``DELETE /api/v1/{resource_type}/{id}/publish``.

Per-entry fields:
  table       — the Postgres table holding both the personal source and the
                ``scope='shared'`` projection row.
  pk_col      — the table's primary-key column.
  pk_type     — ``"uuid"`` or ``"int"`` (only ``files``/``file_index`` is int).
  unproject   — the per-type teardown that removes the shared projection row +
                its Qdrant mirror (used by admin override unshare, LUM-584).
  collection  — the Qdrant collection the shared points live in.
  label_col   — a human-meaningful column for list views (best-effort label).
  shared_ts_col — the timestamp column used as "shared at" in list views. Not
                uniform across tables (``file_index`` has no ``created_at`` —
                it uses ``updated_at``; the projection is upserted with
                ``updated_at = NOW()`` so it approximates the share time), so
                it is pinned per type rather than assumed.

Consumers:
  * ``services.admin_unshare`` — admin retract (needs unproject + collection).
  * ``services.shared_items``  — the member's own shared-items list (needs
                                 table + label_col only).
"""
from __future__ import annotations

from typing import Any
from typing import Optional

from services import projection as proj

SHAREABLE_RESOURCES: dict[str, dict[str, Any]] = {
    "notes": {
        "table": "notes",
        "pk_col": "note_id",
        "pk_type": "uuid",
        "unproject": proj.unproject_note,
        "collection": "conversations",
        "label_col": "text",
        "shared_ts_col": "created_at",
    },
    "audio_memos": {
        "table": "audio_memos",
        "pk_col": "audio_id",
        "pk_type": "uuid",
        "unproject": proj.unproject_audio_memo,
        "collection": "conversations",
        "label_col": "transcript",
        "shared_ts_col": "created_at",
    },
    "sessions": {
        "table": "sessions",
        "pk_col": "session_id",
        "pk_type": "uuid",
        "unproject": proj.unproject_session,
        "collection": "conversations",
        "label_col": "summary",
        "shared_ts_col": "created_at",
    },
    # Public route segment is /files/ but the underlying table is file_index.
    "files": {
        "table": "file_index",
        "pk_col": "id",
        "pk_type": "int",
        "unproject": proj.unproject_file,
        "collection": "documents",
        "label_col": "file_path",
        "shared_ts_col": "updated_at",
    },
    "entities": {
        "table": "entities",
        "pk_col": "entity_id",
        "pk_type": "uuid",
        "unproject": proj.unproject_entity,
        "collection": "entities",
        "label_col": "name",
        "shared_ts_col": "created_at",
    },
    "signals": {
        "table": "signals",
        "pk_col": "signal_id",
        "pk_type": "uuid",
        "unproject": proj.unproject_signal,
        "collection": "signals",
        "label_col": "title",
        "shared_ts_col": "created_at",
    },
}

# The route-segment names, in a stable order — usable for a Pydantic Literal
# and for iterating the shareable arms.
RESOURCE_TYPES: tuple[str, ...] = tuple(SHAREABLE_RESOURCES)


def short_label(value: Any, limit: int = 120) -> Optional[str]:
    """Trim a label column to a compact, single-value snippet for list views."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"
