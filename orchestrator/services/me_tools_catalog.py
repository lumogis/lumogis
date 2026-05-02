# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Read-only ``/api/v1/me/tools`` façade over :class:`services.unified_tools.ToolCatalog`.

No tool execution, no credential material, no raw JSON Schema payloads.
``permission_mode`` is filled by :func:`services.unified_tools.build_tool_catalog_for_user`.
"""

from __future__ import annotations

from collections.abc import Callable

from models.api_v1 import MeToolsItem
from models.api_v1 import MeToolsResponse
from models.api_v1 import MeToolsSummary
from services.unified_tools import ToolCatalog
from services.unified_tools import ToolCatalogEntry
from services.unified_tools import build_tool_catalog_for_user


def _humanize_tool_name(name: str) -> str:
    return name.replace(".", " ").replace("_", " ").strip().title()


def _safe_plain_description(entry: ToolCatalogEntry) -> str:
    """Extract a user-facing description string; never return schema JSON."""
    ts = entry.tool_schema
    if not isinstance(ts, dict):
        return ""
    d = ts.get("description")
    if isinstance(d, str) and d.strip():
        return d.strip()[:4000]
    return ""


def _label_for_entry(entry: ToolCatalogEntry) -> str:
    desc = _safe_plain_description(entry)
    if not desc:
        return _humanize_tool_name(entry.name)
    first = desc.split(". ")[0].strip()
    if len(first) > 120:
        return first[:117] + "…"
    return first or _humanize_tool_name(entry.name)


def _requires_credentials_flag(entry: ToolCatalogEntry) -> bool:
    if entry.requires_credentials is None:
        return False
    return bool(entry.requires_credentials)


def _entry_to_item(entry: ToolCatalogEntry) -> MeToolsItem:
    return MeToolsItem(
        name=entry.name,
        label=_label_for_entry(entry),
        description=_safe_plain_description(entry),
        source=entry.source,
        transport=entry.transport,
        origin_tier=entry.origin_tier,
        available=entry.available,
        why_not_available=entry.why_not_available,
        capability_id=entry.capability_id,
        connector=entry.connector,
        action_type=entry.action_type,
        permission_mode=entry.permission_mode,
        requires_credentials=_requires_credentials_flag(entry),
    )


def _summary_for(entries: tuple[ToolCatalogEntry, ...]) -> MeToolsSummary:
    total = len(entries)
    available = sum(1 for e in entries if e.available)
    by_source: dict[str, int] = {}
    for e in entries:
        by_source[e.source] = by_source.get(e.source, 0) + 1
    # Deterministic key order for stable JSON snapshots
    by_source_sorted = dict(sorted(by_source.items()))
    return MeToolsSummary(
        total=total,
        available=available,
        unavailable=total - available,
        by_source=by_source_sorted,
    )


def build_me_tools_response(
    user_id: str,
    *,
    catalog_builder: Callable[..., ToolCatalog] | None = None,
) -> MeToolsResponse:
    """Build the wire DTO for ``GET /api/v1/me/tools``.

    ``catalog_builder`` defaults to :func:`build_tool_catalog_for_user` and is
    injectable in unit tests.
    """
    builder: Callable[..., ToolCatalog] = catalog_builder or build_tool_catalog_for_user
    catalog: ToolCatalog = builder(user_id=user_id)
    items = [_entry_to_item(e) for e in catalog.entries]
    return MeToolsResponse(tools=items, summary=_summary_for(catalog.entries))
