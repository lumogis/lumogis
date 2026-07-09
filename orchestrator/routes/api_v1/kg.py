# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Knowledge-graph endpoints for the v1 façade.

Three read-only routes backed by Postgres (entity cards, relations, search):

* ``GET /api/v1/kg/entities/{entity_id}`` —
  scoped by :func:`visibility.visible_filter`.
* ``GET /api/v1/kg/entities/{entity_id}/related``
* ``GET /api/v1/kg/search`` — substring search wrapping
  :func:`services.entities.search_by_name`.

:func:`_graph_mode_guard` uses :func:`config.get_graph_mode` (**effective**
mode): returns ``502 kg_unavailable`` only when Core is genuinely wired in
``service`` mode so the SPA fails closed rather than masking a KG-only façade.
Operators who intend full service-mode KG HTTP must provision the premium KG
overlay; degraded wiring resolves to ``disabled`` and Postgres routes continue.
"""

from __future__ import annotations

import logging

from auth import UserContext
from auth import get_user
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi import status
from models.api_v1 import EntityCard
from models.api_v1 import EntitySearchResponse
from models.api_v1 import RelatedEntitiesResponse
from models.api_v1 import RelatedEntity
from visibility import visible_filter

import config

_log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/kg",
    tags=["v1-kg"],
    dependencies=[Depends(require_user)],
)


def _graph_mode_guard() -> None:
    """Fail closed when KG v1 façade cannot serve Postgres-backed reads.

    Uses :func:`config.get_graph_mode` (effective Core decision). When the raw
    operator env asks for ``GRAPH_MODE=service`` but degraded wiring resolves to
    ``disabled``, Postgres-backed handlers continue (no stale raw-env mismatch).
    """
    if config.get_graph_mode() == "service":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "kg_unavailable"},
        )


def _fetch_shared_entity_source_ids(user_id: str) -> set[str]:
    """Source entity ids the caller currently has a shared projection for (LUM-581).

    Mirrors :func:`services.documents._fetch_shared_source_ids`. A shared
    projection row carries the publisher's ``user_id`` and a ``published_from``
    pointing at the personal source; this returns the set of those source ids so
    an owner's personal entity can be marked ``share_status='shared'``.
    """
    ms = config.get_metadata_store()
    try:
        rows = ms.fetch_all(
            # SCOPE-EXEMPT: caller-owned shared entity projection sources.
            "SELECT DISTINCT published_from FROM entities "
            "WHERE user_id = %s AND scope = 'shared' AND published_from IS NOT NULL",
            (user_id,),
        )
    except Exception:  # noqa: BLE001 — DB outage → treat as no shared projections
        _log.warning(
            "kg._fetch_shared_entity_source_ids: query failed user_id=%s",
            user_id,
            exc_info=True,
        )
        return set()
    return {str(r["published_from"]) for r in rows if r.get("published_from") is not None}


def _derive_entity_share_fields(
    row: dict,
    *,
    caller_user_id: str,
    shared_source_ids: set[str],
) -> tuple[str, bool]:
    """Return ``(share_status, is_owner)`` for an entity row (LUM-581).

    Synchronous analogue of :func:`services.documents._derive_share_fields`:
    entity publish has no background job, so only ``personal``/``shared`` occur.

    * A shared projection carries the *publisher's* ``user_id``. A member viewing
      another owner's projection (``scope='shared'`` and not the caller) →
      ``('shared', False)``.
    * The owner's personal source is ``shared`` when a projection exists for it
      (its id is in ``shared_source_ids``), else ``personal``.
    """
    is_owner = row.get("user_id") == caller_user_id
    scope = row.get("scope") or "personal"
    if scope == "shared" and not is_owner:
        return "shared", False
    if str(row.get("entity_id")) in shared_source_ids:
        return "shared", is_owner
    if scope == "shared" and is_owner:
        # A projection row the caller owns fetched directly by id (the list path
        # collapses these away; the single-fetch path may still hit one).
        return "shared", True
    return "personal", is_owner


def _row_to_card(
    row: dict,
    *,
    share_status: str = "personal",
    is_owner: bool = True,
) -> EntityCard:
    return EntityCard(
        entity_id=str(row["entity_id"]),
        name=row["name"],
        type=row.get("entity_type"),
        aliases=list(row.get("aliases") or []),
        summary=row.get("summary"),
        sources=list(row.get("sources") or []),
        scope=row.get("scope", "personal"),
        owner_user_id=row.get("user_id") if row.get("scope") in {"shared", "system"} else None,
        share_status=share_status,  # type: ignore[arg-type]
        is_owner=is_owner,
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
            "       mention_count, scope, user_id, published_from "
            "FROM entities WHERE " + where_clause + " AND entity_id::text = %s "
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
    share_status, is_owner = _derive_entity_share_fields(
        row,
        caller_user_id=user_id,
        shared_source_ids=_fetch_shared_entity_source_ids(user_id),
    )
    return _row_to_card(row, share_status=share_status, is_owner=is_owner)


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
            entity_id,
            exc_info=True,
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
            "WHERE (es.entity_id_a::text = %s OR es.entity_id_b::text = %s) AND " + e_where + " "
            "ORDER BY es.edge_quality DESC NULLS LAST "
            "LIMIT %s",
            (entity_id, entity_id, entity_id, *where_params, limit),
        )
    except Exception:  # noqa: BLE001 — edge_scores may be empty / missing in fresh installs.
        # Soft-fail with [] so the SPA renders an empty state, not a 500.
        _log.info(
            "kg.related_entities: edge_scores query failed; returning empty. entity_id=%s",
            entity_id,
            exc_info=True,
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
            "SELECT entity_id, name, entity_type, aliases, mention_count, scope, "
            "       user_id, published_from "
            "FROM entities WHERE " + where_clause + " AND name ILIKE %s "
            # LUM-581 collapse: hide the caller's OWN shared projection rows so
            # they see one entity (the personal source, marked share_status
            # 'shared'), not a duplicate. A projection carries the publisher's
            # user_id, so other members' shared rows (user_id != caller) are kept.
            "AND NOT (published_from IS NOT NULL AND user_id = %s) "
            "ORDER BY mention_count DESC "
            "LIMIT %s",
            (*where_params, pattern, user_id, limit),
        )
    except Exception:  # noqa: BLE001 — DB outage → empty answer
        _log.warning("kg.search: DB query failed q=%r", q, exc_info=True)
        return EntitySearchResponse(entities=[])

    shared_source_ids = _fetch_shared_entity_source_ids(user_id)
    cards: list[EntityCard] = []
    for r in rows:
        share_status, is_owner = _derive_entity_share_fields(
            r,
            caller_user_id=user_id,
            shared_source_ids=shared_source_ids,
        )
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
                share_status=share_status,  # type: ignore[arg-type]
                is_owner=is_owner,
            )
        )
    return EntitySearchResponse(entities=cards)
