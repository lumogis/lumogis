# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Wow-moment readiness read model (LUM-216).

Computes ``entities_ready`` and top entities for ``GET /api/v1/me/wow-state``.
Uses household ``visible_filter`` plus non-staged exclusion (differs from
``GET /entities``, which still lists staged rows).
"""

from __future__ import annotations

import uuid
from typing import Any

from auth import UserContext
from visibility import visible_filter

import config

_STAGED_CLAUSE = "AND (is_staged IS NOT TRUE)"


def _entity_where(user_id: str, scope_filter: str | None = None) -> tuple[str, tuple]:
    vis_clause, vis_params = visible_filter(UserContext(user_id=user_id), scope_filter)
    return f"({vis_clause}) {_STAGED_CLAUSE}", vis_params


def count_visible_entities(user_id: str) -> int:
    """Return count of non-staged entities visible to ``user_id`` (household union)."""
    where, params = _entity_where(user_id)
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        f"SELECT COUNT(*)::int AS n FROM entities WHERE {where}",
        params,
    )
    if row is None:
        return 0
    return int(row.get("n") or 0)


def list_top_entities(user_id: str, limit: int = 5) -> list[dict[str, Any]]:
    """Return up to ``limit`` entities ordered by mention_count descending."""
    where, params = _entity_where(user_id)
    ms = config.get_metadata_store()
    rows = ms.fetch_all(
        "SELECT entity_id, name, entity_type, mention_count, scope "
        f"FROM entities WHERE {where} "
        "ORDER BY mention_count DESC "
        "LIMIT %s",
        (*params, limit),
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        eid = r.get("entity_id")
        out.append(
            {
                "entity_id": str(eid) if eid is not None else str(uuid.uuid4()),
                "name": r["name"],
                "entity_type": r["entity_type"],
                "mention_count": int(r.get("mention_count") or 0),
                "scope": r.get("scope", "personal"),
            }
        )
    return out


def get_wow_state(user_id: str) -> dict[str, Any]:
    """Aggregate wow read model for route layer."""
    count = count_visible_entities(user_id)
    return {
        "entities_ready": count >= 1,
        "top_entities": list_top_entities(user_id, 5) if count >= 1 else [],
    }
