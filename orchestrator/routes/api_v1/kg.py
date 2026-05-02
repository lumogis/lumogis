# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Knowledge-graph endpoints for the v1 façade.

Three read-only routes:

* ``GET /api/v1/kg/entities/{entity_id}`` — entity card by UUID, scoped
  by :func:`visibility.visible_filter`.
* ``GET /api/v1/kg/entities/{entity_id}/related`` — first-degree
  relations from ``entity_relations``.
* ``GET /api/v1/kg/search`` — substring search wrapping
  :func:`services.entities.search_by_name`.

Phase 0 ships an in-process implementation only — the planned
``GRAPH_MODE=service`` httpx proxy is **deferred** until the
``lumogis-graph`` service exists. The route module returns
``502 {"error":"kg_unavailable"}`` if ``GRAPH_MODE=service`` is set today
so the SPA fails closed instead of silently falling back to inprocess.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

import config
from auth import UserContext, get_user
from authz import require_user
from models.api_v1 import (
    EntityCard,
    EntitySearchResponse,
    RelatedEntitiesResponse,
    RelatedEntity,
)
from visibility import visible_filter

_log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/kg",
    tags=["v1-kg"],
    dependencies=[Depends(require_user)],
)


def _graph_mode_guard() -> None:
    """Fail closed when an operator sets ``GRAPH_MODE=service`` in v1.

    The plan calls for an httpx proxy in service-mode, but the
    ``lumogis-graph`` HTTP service does not ship in Phase 0. Returning
    502 is louder than silently falling back to inprocess (which would
    mask a misconfiguration in a household running the graph service for
    other consumers).
    """
    if os.environ.get("GRAPH_MODE", "inprocess").lower() == "service":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "kg_unavailable"},
        )


def _row_to_card(row: dict) -> EntityCard:
    return EntityCard(
        entity_id=str(row["entity_id"]),
        name=row["name"],
        type=row.get("entity_type"),
        aliases=list(row.get("aliases") or []),
        summary=row.get("summary"),
        sources=list(row.get("sources") or []),
        scope=row.get("scope", "personal"),
        owner_user_id=row.get("user_id") if row.get("scope") in {"shared", "system"} else None,
    )


@router.get("/entities/{entity_id}", response_model=EntityCard)
def get_entity(entity_id: str, request: Request) -> EntityCard:
    _graph_mode_guard()
    user_id = get_user(request).user_id
    ms = config.get_metadata_store()
    user = UserContext(user_id=user_id)
    where_clause, where_params = visible_filter(user, scope_filter=None)
    try:
        row = ms.fetch_one(
            "SELECT entity_id, name, entity_type, aliases, context_tags, "
            "       mention_count, scope, user_id "
            "FROM entities WHERE "
            + where_clause
            + " AND entity_id::text = %s "
            "LIMIT 1",
            (*where_params, entity_id),
        )
    except Exception:  # noqa: BLE001 — DB outage → empty answer
        _log.warning("kg.get_entity: DB query failed for entity_id=%s", entity_id, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "entity_not_found"},
        )

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "entity_not_found"},
        )
    return _row_to_card(row)


@router.get("/entities/{entity_id}/related", response_model=RelatedEntitiesResponse)
def related_entities(
    entity_id: str,
    request: Request,
    limit: int = Query(20, ge=1, le=50),
) -> RelatedEntitiesResponse:
    _graph_mode_guard()
    user_id = get_user(request).user_id
    ms = config.get_metadata_store()
    user = UserContext(user_id=user_id)
    where_clause, where_params = visible_filter(user, scope_filter=None)

    # Confirm the source entity is visible — otherwise we'd leak related
    # rows for an entity the caller cannot itself see (404 mirrors get_entity).
    try:
        head = ms.fetch_one(
            "SELECT entity_id FROM entities WHERE "
            + where_clause
            + " AND entity_id::text = %s LIMIT 1",
            (*where_params, entity_id),
        )
    except Exception:  # noqa: BLE001
        _log.warning(
            "kg.related_entities: head visibility query failed for entity_id=%s",
            entity_id, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "entity_not_found"},
        )
    if head is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "entity_not_found"},
        )

    # Co-occurrence-based related lookup using edge_scores (entity_id_a/b).
    # The shipped `entity_relations` table only stores entity→evidence edges,
    # not entity→entity. `edge_scores` is the canonical entity-to-entity
    # graph (PPMI / edge_quality from the weekly quality job, see
    # postgres/init.sql §edge_scores).
    e_where = where_clause.replace("user_id", "e.user_id").replace("scope", "e.scope")
    try:
        rows = ms.fetch_all(
            "SELECT e.entity_id AS entity_id, e.name AS name, "
            "       'CO_OCCURS' AS relation, es.edge_quality AS weight "
            "FROM edge_scores es "
            "JOIN entities e ON e.entity_id::text = "
            "  CASE WHEN es.entity_id_a::text = %s THEN es.entity_id_b::text "
            "       ELSE es.entity_id_a::text END "
            "WHERE (es.entity_id_a::text = %s OR es.entity_id_b::text = %s) AND "
            + e_where
            + " "
            "ORDER BY es.edge_quality DESC NULLS LAST "
            "LIMIT %s",
            (entity_id, entity_id, entity_id, *where_params, limit),
        )
    except Exception:  # noqa: BLE001 — edge_scores may be empty / missing in fresh installs.
        # Soft-fail with [] so the SPA renders an empty state, not a 500.
        _log.info(
            "kg.related_entities: edge_scores query failed; returning empty. entity_id=%s",
            entity_id, exc_info=True,
        )
        return RelatedEntitiesResponse(related=[])

    return RelatedEntitiesResponse(
        related=[
            RelatedEntity(
                entity_id=str(r["entity_id"]),
                name=r["name"],
                relation=r["relation"],
                weight=r.get("weight"),
            )
            for r in rows
        ]
    )


@router.get("/search", response_model=EntitySearchResponse)
def search(
    request: Request,
    q: str = Query(..., min_length=1, max_length=512),
    limit: int = Query(10, ge=1, le=50),
) -> EntitySearchResponse:
    _graph_mode_guard()
    user_id = get_user(request).user_id

    # Direct query rather than `services.entities.search_by_name` because
    # the public helper drops `entity_id` from its result rows (legacy
    # MCP tool that uses name-as-key). The web façade needs the UUID so
    # the SPA can navigate to the entity card.
    ms = config.get_metadata_store()
    user = UserContext(user_id=user_id)
    where_clause, where_params = visible_filter(user, scope_filter=None)
    pattern = f"%{q.strip()}%"
    try:
        rows = ms.fetch_all(
            "SELECT entity_id, name, entity_type, aliases, mention_count, scope, user_id "
            "FROM entities WHERE "
            + where_clause
            + " AND name ILIKE %s "
            "ORDER BY mention_count DESC "
            "LIMIT %s",
            (*where_params, pattern, limit),
        )
    except Exception:  # noqa: BLE001 — DB outage → empty answer
        _log.warning("kg.search: DB query failed q=%r", q, exc_info=True)
        return EntitySearchResponse(entities=[])

    cards: list[EntityCard] = []
    for r in rows:
        cards.append(
            EntityCard(
                entity_id=str(r["entity_id"]),
                name=r["name"],
                type=r.get("entity_type"),
                aliases=list(r.get("aliases") or []),
                summary=None,
                sources=[],
                scope=r.get("scope", "personal"),
                owner_user_id=r.get("user_id") if r.get("scope") in {"shared", "system"} else None,
            )
        )
    return EntitySearchResponse(entities=cards)
