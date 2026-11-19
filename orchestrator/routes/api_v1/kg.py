# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Knowledge-graph endpoints for the v1 façade.

Fifteen routes backed by Postgres (entity cards, relations, search, and the
"What Lumogis knows" browser):

* ``GET /api/v1/kg/entities`` — the entries the caller may see, by kind (LUM-161).
* ``GET /api/v1/kg/entities/{entity_id}`` —
  scoped by :func:`visibility.visible_filter`.
* ``GET /api/v1/kg/entities/{entity_id}/mentions`` — items that mention an entry
  and that the caller can open (LUM-161).
* ``PATCH /api/v1/kg/entities/{entity_id}`` — rename an entry, edit its other
  names, or change its kind; the router's one write route. The write goes through
  :mod:`services.entity_edit` (LUM-161).
* ``POST /api/v1/kg/entities/{entity_id}/merge`` — merge another of the caller's own
  entries into this one (LUM-965); every merge records what it moved.
* ``GET /api/v1/kg/entities/{entity_id}/history`` — merges into this entry, with
  what can be undone (LUM-965).
* ``POST /api/v1/kg/merges/{merge_id}/undo`` — undo a merge, newest first per entry
  (LUM-965).
* ``POST /api/v1/kg/entities/{entity_id}/mentions/move`` — move mentions between
  two of the caller's own entries (LUM-965).
* ``DELETE /api/v1/kg/entities/{entity_id}`` — delete the caller's own entry; its
  names go on the member's list of names Lumogis won't learn (LUM-966).
* ``POST`` / ``DELETE /api/v1/kg/entities/{entity_id}/hide`` — hide (and show again)
  another member's shared entry in the caller's own view (LUM-966).
* ``GET /api/v1/kg/name-rules`` and ``POST /api/v1/kg/name-rules/remove`` — the list
  of names Lumogis won't learn, kind corrections and hidden entries; removing a
  name is the undo of a delete (LUM-966).
* ``GET /api/v1/kg/entities/{entity_id}/related``
* ``GET /api/v1/kg/search`` — substring search wrapping
  :func:`services.entities.search_by_name`.

All routes read Postgres only and serve the same handler and query in
every effective ``GRAPH_MODE`` (``disabled``, ``inprocess``, ``service``):
entities are written to Postgres in every mode and the graph service shares
that database. A future route that reads FalkorDB or the graph service handles
that dependency's outage in its own handler and must not gate the router on
``GRAPH_MODE`` (LUM-964, ADR draft ``LUM-964-kg_entity_routes_graph_mode``).
The graph follows a rename or kind change at the next reconcile pass (ADR 263).
"""

from __future__ import annotations

import logging
from typing import Literal
from typing import Optional

from auth import UserContext
from auth import get_user
from authz import require_user
from csrf import require_same_origin
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi import status
from models.api_v1 import EntityCard
from models.api_v1 import EntityDeleteResponse
from models.api_v1 import EntityEditRequest
from models.api_v1 import EntityHideResponse
from models.api_v1 import EntityHistoryResponse
from models.api_v1 import EntityKind
from models.api_v1 import EntityListResponse
from models.api_v1 import EntityMentionsResponse
from models.api_v1 import EntityMergeHistoryItem
from models.api_v1 import EntityMergeRequest
from models.api_v1 import EntityMergeResponse
from models.api_v1 import EntityMoveMentionsRequest
from models.api_v1 import EntityMoveMentionsResponse
from models.api_v1 import EntityNotBlocked
from models.api_v1 import EntitySearchResponse
from models.api_v1 import EntityUndoMergeResponse
from models.api_v1 import NameRulesResponse
from models.api_v1 import RelatedEntitiesResponse
from models.api_v1 import RelatedEntity
from models.api_v1 import RemoveNameRuleRequest
from models.api_v1 import RemoveNameRuleResponse
from services.entity_browser import EntityStoreUnavailable
from services.entity_browser import _derive_entity_share_fields
from services.entity_browser import _fetch_shared_entity_source_ids
from visibility import _effective_allows_shared
from visibility import not_hidden_sql
from visibility import visible_filter

import config
from services import entity_browser
from services import entity_edit

_log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/kg",
    tags=["v1-kg"],
    dependencies=[Depends(require_user)],
)


def _viewer(request: Request) -> UserContext:
    """The viewer context every handler builds (LUM-161, Decision 12).

    An *unauthenticated* ``UserContext`` so ``allows_shared`` is honoured even
    for an admin (the household union, never god-mode). ``allows_shared`` is
    resolved once here with the function ``visible_filter`` uses, so the several
    ``visible_filter`` calls of one request do not each read ``users``.
    """
    user_id = get_user(request).user_id
    return UserContext(
        user_id=user_id,
        allows_shared=_effective_allows_shared(UserContext(user_id=user_id)),
    )


def _error(status_code: int, code: str, **extra) -> HTTPException:
    """v1 domain error: ``{"detail": {"error": code, "code": code, ...}}``.

    ``ApiError.code`` in Lumogis Web is read from ``detail.code`` (LUM-759).
    """
    return HTTPException(status_code=status_code, detail={"error": code, "code": code, **extra})


def _viewer_or_503(request: Request) -> UserContext:
    try:
        return _viewer(request)
    except Exception as exc:  # noqa: BLE001 — users lookup outage
        _log.warning("kg: viewer context unavailable", exc_info=True)
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "store_unavailable") from exc


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
        version=int(row["version"]) if row.get("version") is not None else None,
    )


@router.get("/entities", response_model=EntityListResponse)
def list_entities_route(
    request: Request,
    kind: Optional[EntityKind] = Query(None),
    shared: Optional[Literal["only", "exclude"]] = Query(None),
    sort: Literal["mentions", "recent", "name"] = Query("mentions"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10000),
) -> EntityListResponse:
    """The entries the caller may see, grouped by kind, with counts (LUM-161)."""
    user = _viewer_or_503(request)
    try:
        return entity_browser.list_entities(
            user, kind=kind, shared=shared, sort=sort, limit=limit, offset=offset
        )
    except EntityStoreUnavailable as exc:
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "store_unavailable") from exc


@router.get("/entities/{entity_id}/mentions", response_model=EntityMentionsResponse)
def entity_mentions(
    entity_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10000),
) -> EntityMentionsResponse:
    """Items that mention an entry **and that the caller can open** (LUM-161)."""
    user = _viewer_or_503(request)
    try:
        result = entity_browser.list_mentions(user, entity_id, limit=limit, offset=offset)
    except EntityStoreUnavailable as exc:
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "store_unavailable") from exc
    if result is None:
        raise _error(status.HTTP_404_NOT_FOUND, "entity_not_found")
    return result


@router.patch(
    "/entities/{entity_id}",
    response_model=EntityCard,
    dependencies=[Depends(require_same_origin)],
)
def edit_entity(entity_id: str, body: EntityEditRequest, request: Request) -> EntityCard:
    """Rename an entry, change its other names, or change its kind (owner only).

    One change at a time: ``entity_type`` (LUM-967) cannot be combined with a
    name or other-name change.
    """
    kind_change = body.entity_type is not None
    rename = body.name is not None or bool(body.aliases_add) or bool(body.aliases_remove)
    if kind_change and rename:
        raise _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "one_change_at_a_time")
    if not kind_change and not rename:
        raise _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "nothing_to_change")
    user = _viewer_or_503(request)
    try:
        if kind_change:
            row = entity_edit.change_entity_kind(
                user, entity_id, entity_type=body.entity_type, version=body.version
            )
        else:
            row = entity_edit.rename_entity(
                user,
                entity_id,
                name=body.name,
                aliases_add=body.aliases_add,
                aliases_remove=body.aliases_remove,
                version=body.version,
            )
    except entity_edit.EntityNotFound as exc:
        raise _error(status.HTTP_404_NOT_FOUND, "entity_not_found") from exc
    except entity_edit.EntityNotOwner as exc:
        raise _error(status.HTTP_403_FORBIDDEN, "not_owner") from exc
    except entity_edit.EntityNotEditable as exc:
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "not_editable",
            source_entity_id=exc.source_entity_id,
        ) from exc
    except entity_edit.EntityVersionConflict as exc:
        raise _error(
            status.HTTP_409_CONFLICT, "version_conflict", current_version=exc.current_version
        ) from exc
    except entity_edit.EntityNameInUse as exc:
        raise _error(
            status.HTTP_409_CONFLICT,
            "name_in_use",
            conflicting_entity_id=exc.conflicting_entity_id,
            conflicting_name=exc.conflicting_name,
            name=exc.surface_form,
            conflicting_is_staged=exc.conflicting_is_staged,
            conflicting_entity_type=exc.conflicting_entity_type,
        ) from exc
    except entity_edit.EntityKindInvalid as exc:
        raise _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_kind") from exc
    except entity_edit.EntityNameInvalid as exc:
        raise _error(status.HTTP_422_UNPROCESSABLE_ENTITY, exc.reason) from exc
    except EntityStoreUnavailable as exc:
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "store_unavailable") from exc
    share_status, is_owner = _derive_entity_share_fields(
        row,
        caller_user_id=user.user_id,
        shared_source_ids=_fetch_shared_entity_source_ids(user.user_id),
    )
    return _row_to_card(row, share_status=share_status, is_owner=is_owner)


def _merge_error(exc: Exception) -> HTTPException:
    """Map the member write path's refusals to v1 domain errors (LUM-965).

    Head-check refusals carry ``which`` (``"this"`` = the path entry, ``"other"``
    = the other one) so the dialog can name the entry that cannot be merged.
    """
    which = getattr(exc, "which", "this")
    if isinstance(exc, entity_edit.EntityNotFound):
        return _error(status.HTTP_404_NOT_FOUND, "entity_not_found", which=which)
    if isinstance(exc, entity_edit.EntityNotOwner):
        return _error(status.HTTP_403_FORBIDDEN, "not_owner", which=which)
    if isinstance(exc, entity_edit.EntityNotEditable):
        return _error(
            status.HTTP_403_FORBIDDEN,
            "not_editable",
            source_entity_id=exc.source_entity_id,
            which=which,
        )
    if isinstance(exc, entity_edit.EntityVersionConflict):
        return _error(
            status.HTTP_409_CONFLICT,
            "version_conflict",
            current_version=exc.current_version,
            which=which,
        )
    if isinstance(exc, entity_edit.EntitySameEntry):
        return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "same_entry")
    if isinstance(exc, entity_edit.EntityStaged):
        return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "staged_entry", which=which)
    if isinstance(exc, entity_edit.MentionsNotFound):
        return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "relation_not_found")
    if isinstance(exc, entity_edit.EntityHideOwn):
        return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "own_entry")
    if isinstance(exc, entity_edit.NameRuleNotFound):
        return _error(status.HTTP_404_NOT_FOUND, "rule_not_found")
    if isinstance(exc, entity_edit.MergeNotFound):
        return _error(status.HTTP_404_NOT_FOUND, "merge_not_found")
    if isinstance(exc, entity_edit.MergeAlreadyUndone):
        return _error(status.HTTP_409_CONFLICT, "already_undone")
    if isinstance(exc, entity_edit.MergeNotNewest):
        return _error(
            status.HTTP_409_CONFLICT,
            "undo_newer_first",
            newer_merge_id=exc.newer_merge_id,
            newer_other_name=exc.newer_other_name,
        )
    if isinstance(exc, entity_edit.MergeLoserIdTaken):
        return _error(status.HTTP_409_CONFLICT, "loser_id_taken")
    if isinstance(exc, EntityStoreUnavailable):
        return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "store_unavailable")
    raise exc


_MERGE_ERRORS = (
    entity_edit.EntityNotFound,
    entity_edit.EntityNotOwner,
    entity_edit.EntityNotEditable,
    entity_edit.EntityVersionConflict,
    entity_edit.EntitySameEntry,
    entity_edit.EntityStaged,
    entity_edit.MentionsNotFound,
    entity_edit.EntityHideOwn,
    entity_edit.NameRuleNotFound,
    entity_edit.MergeNotFound,
    entity_edit.MergeAlreadyUndone,
    entity_edit.MergeNotNewest,
    entity_edit.MergeLoserIdTaken,
    EntityStoreUnavailable,
)


def _card(row: dict, user: UserContext) -> EntityCard:
    share_status, is_owner = _derive_entity_share_fields(
        row,
        caller_user_id=user.user_id,
        shared_source_ids=_fetch_shared_entity_source_ids(user.user_id),
    )
    return _row_to_card(row, share_status=share_status, is_owner=is_owner)


@router.post(
    "/entities/{entity_id}/merge",
    response_model=EntityMergeResponse,
    dependencies=[Depends(require_same_origin)],
)
def merge_entity(entity_id: str, body: EntityMergeRequest, request: Request) -> EntityMergeResponse:
    """Merge another of the caller's own entries into this one (this entry survives).

    Records what moved so the merge can be undone from the entry's History.
    """
    user = _viewer_or_503(request)
    try:
        row, merge_id = entity_edit.merge_entries(
            user,
            entity_id,
            body.other_id,
            version=body.version,
            other_version=body.other_version,
            keep_name=body.keep_name,
        )
    except _MERGE_ERRORS as exc:
        raise _merge_error(exc) from exc
    return EntityMergeResponse(entry=_card(row, user), merge_id=merge_id)


@router.get("/entities/{entity_id}/history", response_model=EntityHistoryResponse)
def entity_history(entity_id: str, request: Request) -> EntityHistoryResponse:
    """Merges into (and, once undone, out of) this entry, newest first (owner only)."""
    user = _viewer_or_503(request)
    try:
        items = entity_edit.list_merge_history(user, entity_id)
    except _MERGE_ERRORS as exc:
        raise _merge_error(exc) from exc
    return EntityHistoryResponse(
        items=[
            EntityMergeHistoryItem(**{k: v for k, v in item.items() if k != "newer_other_name"})
            for item in items
        ]
    )


@router.post(
    "/merges/{merge_id}/undo",
    response_model=EntityUndoMergeResponse,
    dependencies=[Depends(require_same_origin)],
)
def undo_merge(merge_id: str, request: Request) -> EntityUndoMergeResponse:
    """Undo one of the caller's merges (newest first per entry)."""
    user = _viewer_or_503(request)
    try:
        restored, survivor, since, skipped = entity_edit.undo_merge(user, merge_id)
    except _MERGE_ERRORS as exc:
        raise _merge_error(exc) from exc
    return EntityUndoMergeResponse(
        restored=_card(restored, user),
        survivor=_card(survivor, user),
        mentions_since=since,
        skipped_rows=skipped,
    )


@router.post(
    "/entities/{entity_id}/mentions/move",
    response_model=EntityMoveMentionsResponse,
    dependencies=[Depends(require_same_origin)],
)
def move_entity_mentions(
    entity_id: str, body: EntityMoveMentionsRequest, request: Request
) -> EntityMoveMentionsResponse:
    """Move mentions (by id) from this entry to another of the caller's own entries."""
    user = _viewer_or_503(request)
    try:
        src, dst, moved = entity_edit.move_mentions(
            user,
            entity_id,
            body.to_entity_id,
            relation_ids=body.relation_ids,
            version=body.version,
            to_version=body.to_version,
        )
    except _MERGE_ERRORS as exc:
        raise _merge_error(exc) from exc
    return EntityMoveMentionsResponse(moved=moved, source=_card(src, user), target=_card(dst, user))


@router.delete(
    "/entities/{entity_id}",
    response_model=EntityDeleteResponse,
    dependencies=[Depends(require_same_origin)],
)
def delete_entity(
    entity_id: str, request: Request, version: int = Query(..., ge=0)
) -> EntityDeleteResponse:
    """Delete the caller's own entry and keep Lumogis from learning its names again (LUM-966).

    Owner only: another member's personal entry is a 404, the household copy of
    another member's entry is a 403 ``not_owner`` (hide it instead).
    """
    user = _viewer_or_503(request)
    try:
        outcome = entity_edit.delete_entry(user, entity_id, version=version)
    except _MERGE_ERRORS as exc:
        raise _merge_error(exc) from exc
    return EntityDeleteResponse(
        entity_id=outcome.entity_id,
        blocked_names=outcome.blocked_names,
        not_blocked=[EntityNotBlocked(**n) for n in outcome.not_blocked],
        household_copy_removed=outcome.household_copy_removed,
        merges_removed=outcome.merges_removed,
    )


@router.post(
    "/entities/{entity_id}/hide",
    response_model=EntityHideResponse,
    dependencies=[Depends(require_same_origin)],
)
def hide_entity(entity_id: str, request: Request) -> EntityHideResponse:
    """Hide another member's shared entry from the caller's own list, search and chat (LUM-966)."""
    user = _viewer_or_503(request)
    try:
        entity_edit.hide_entry(user, entity_id)
    except _MERGE_ERRORS as exc:
        raise _merge_error(exc) from exc
    return EntityHideResponse(entity_id=entity_id, hidden=True)


@router.delete(
    "/entities/{entity_id}/hide",
    response_model=EntityHideResponse,
    dependencies=[Depends(require_same_origin)],
)
def unhide_entity(entity_id: str, request: Request) -> EntityHideResponse:
    """Show a hidden entry again. Idempotent (LUM-966)."""
    user = _viewer_or_503(request)
    try:
        entity_edit.unhide_entry(user, entity_id)
    except _MERGE_ERRORS as exc:
        raise _merge_error(exc) from exc
    return EntityHideResponse(entity_id=entity_id, hidden=False)


@router.get("/name-rules", response_model=NameRulesResponse)
def list_name_rules(request: Request) -> NameRulesResponse:
    """The caller's names Lumogis won't learn, kind corrections and hidden entries (LUM-966)."""
    user = _viewer_or_503(request)
    try:
        return NameRulesResponse(**entity_edit.list_name_rules(user))
    except _MERGE_ERRORS as exc:
        raise _merge_error(exc) from exc


@router.post(
    "/name-rules/remove",
    response_model=RemoveNameRuleResponse,
    dependencies=[Depends(require_same_origin)],
)
def remove_name_rule(body: RemoveNameRuleRequest, request: Request) -> RemoveNameRuleResponse:
    """Remove a name from the won't-learn list, or a kind correction (LUM-966).

    A POST with a body, not ``DELETE /name-rules/{type}/{name}``: names contain
    spaces, slashes and non-ASCII, and a body avoids path-encoding bugs. Removing
    a name offers a re-read of the documents that mentioned it.
    """
    user = _viewer_or_503(request)
    try:
        result = entity_edit.remove_name_rule(
            user, name_norm=body.name_norm, entity_type=body.entity_type, kind=body.kind
        )
    except _MERGE_ERRORS as exc:
        raise _merge_error(exc) from exc
    return RemoveNameRuleResponse(**result)


@router.get("/entities/{entity_id}", response_model=EntityCard)
def get_entity(entity_id: str, request: Request) -> EntityCard:
    user = _viewer(request)
    user_id = user.user_id
    ms = config.get_metadata_store()
    where_clause, where_params = visible_filter(user, scope_filter=None)
    try:
        row = ms.fetch_one(
            "SELECT entity_id, name, entity_type, aliases, context_tags, "
            "       mention_count, scope, user_id, published_from, version "
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
    card = _row_to_card(row, share_status=share_status, is_owner=is_owner)
    # LUM-966: the full card is authoritative for "hidden by me" (search stubs never carry it).
    try:
        hidden = ms.fetch_one(
            "SELECT 1 AS x FROM entity_hidden "
            # SCOPE-EXEMPT: entity_hidden is a per-member view table (no scope column).
            "WHERE user_id = %s AND entity_key::text = %s LIMIT 1",
            (user_id, str(row.get("published_from") or row["entity_id"])),
        )
        card.hidden = hidden is not None
    except Exception:  # noqa: BLE001 — a missing table must not break the card
        _log.warning("kg.get_entity: hidden lookup failed", exc_info=True)
    return card


# NOTE: ``EntityCard.sources`` stays ``[]`` and is effectively deprecated — "Mentioned
# in" lives on ``GET /entities/{id}/mentions`` (LUM-161). Do not fill it.


@router.get("/entities/{entity_id}/related", response_model=RelatedEntitiesResponse)
def related_entities(
    entity_id: str,
    request: Request,
    limit: int = Query(20, ge=1, le=50),
) -> RelatedEntitiesResponse:
    user = _viewer(request)
    user_id = user.user_id
    ms = config.get_metadata_store()
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
    # postgres/init.sql §edge_scores). Rows are only the caller's own
    # co-occurrence scores (per scoring member), and exist only after the
    # weekly quality job has run (the graph service's job in `service` mode,
    # Core's in `inprocess`, none in `disabled`).
    e_where, e_params = visible_filter(user, scope_filter=None, qualifier="e")
    h_where, h_params = not_hidden_sql(user_id, qualifier="e")
    try:
        rows = ms.fetch_all(
            "SELECT e.entity_id AS entity_id, e.name AS name, "
            "       'CO_OCCURS' AS relation, es.edge_quality AS weight "
            "FROM edge_scores es "
            "JOIN entities e ON e.entity_id::text = "
            "  CASE WHEN es.entity_id_a::text = %s THEN es.entity_id_b::text "
            "       ELSE es.entity_id_a::text END "
            "WHERE (es.entity_id_a::text = %s OR es.entity_id_b::text = %s) "
            # SCOPE-EXEMPT: edge_scores has no scope column; per scoring member (LUM-964).
            "AND es.user_id = %s AND " + e_where + " AND " + h_where + " "
            "ORDER BY es.edge_quality DESC NULLS LAST "
            "LIMIT %s",
            (entity_id, entity_id, entity_id, user_id, *e_params, *h_params, limit),
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
    user = _viewer(request)
    user_id = user.user_id

    # Direct query rather than `services.entities.search_by_name` because
    # the public helper drops `entity_id` from its result rows (legacy
    # MCP tool that uses name-as-key). The web façade needs the UUID so
    # the SPA can navigate to the entity card.
    ms = config.get_metadata_store()
    where_clause, where_params = visible_filter(user, scope_filter=None)
    hidden_clause, hidden_params = not_hidden_sql(user_id)
    pattern = f"%{q.strip()}%"
    try:
        rows = ms.fetch_all(
            "SELECT entity_id, name, entity_type, aliases, mention_count, scope, "
            "       user_id, published_from "
            "FROM entities WHERE " + where_clause + " AND " + hidden_clause + " AND name ILIKE %s "
            # LUM-581 collapse: hide the caller's OWN shared projection rows so
            # they see one entity (the personal source, marked share_status
            # 'shared'), not a duplicate. A projection carries the publisher's
            # user_id, so other members' shared rows (user_id != caller) are kept.
            "AND NOT (published_from IS NOT NULL AND user_id = %s) "
            "ORDER BY mention_count DESC "
            "LIMIT %s",
            (*where_params, *hidden_params, pattern, user_id, limit),
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
