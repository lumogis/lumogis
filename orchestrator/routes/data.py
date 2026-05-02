# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Data endpoints: /ingest, /search, /session/end, /entities/extract, /entities."""

from typing import List
from typing import Optional

from auth import UserContext
from auth import get_user
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from models.sessions import SessionMessage
from pydantic import BaseModel
from pydantic import ValidationError
from services.batch_queue import enqueue
from services.search import semantic_search
from visibility import visible_filter

import config


_ALLOWED_SCOPE_FILTERS = {None, "personal", "shared", "system"}


def _coerce_scope_filter(value: Optional[str]) -> Optional[str]:
    """Validate a `?scope=` query-string value before passing to visible_filter.

    The helper itself raises ValueError on unknown scope strings; we
    convert that into a 400 here so HTTP callers get a clean error
    rather than a 500. Empty string and missing param both mean "no
    narrowing" → returns the household union.
    """
    if value is None or value == "":
        return None
    if value not in _ALLOWED_SCOPE_FILTERS:
        raise HTTPException(
            status_code=400,
            detail=f"scope must be one of personal|shared|system, got {value!r}",
        )
    return value

router = APIRouter()


class IngestRequest(BaseModel):
    path: str = "/data"
    # Admins may attribute the ingest to another user (e.g. populating
    # a family member's collection on their behalf). Standard users
    # cannot set this — it is silently ignored if they try.
    user_id: str | None = None


@router.post("/ingest")
def ingest_endpoint(
    body: IngestRequest,
    user: UserContext = Depends(require_user),
):
    """Kick off a folder ingest attributed to the calling user.

    Phase 3: every chunk and metadata row is tagged with the resolved
    ``user_id``. Standard users always ingest as themselves; only
    admins may pass an explicit ``user_id`` override in the body.
    """
    target_user = user.user_id
    if body.user_id and body.user_id != user.user_id:
        if user.role != "admin":
            raise HTTPException(
                status_code=403,
                detail="Only admins can ingest on behalf of another user.",
            )
        target_user = body.user_id

    try:
        enqueue(user_id=target_user, kind="ingest_folder", payload={"path": body.path})
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    return {"status": "ingest queued", "path": body.path, "user_id": target_user}


@router.get("/search")
def search_endpoint(
    q: str,
    limit: int = 5,
    scope: Optional[str] = Query(default=None, regex="^(personal|shared|system)$"),
    user: UserContext = Depends(require_user),
):
    """Semantic search across the household-visible document corpus.

    Default visibility (no `?scope=`) returns the household union
    (personal-mine + shared + system); narrow with `?scope=personal`,
    `?scope=shared`, or `?scope=system`. Per the headline-test invariant
    (plan §2.8): when an admin calls `?scope=personal`, the visibility
    helper still ANDs in `user_id = $admin_id` — admins do NOT get
    cross-user personal visibility on this surface.
    """
    results = semantic_search(
        q, limit=limit, user_id=user.user_id, scope_filter=_coerce_scope_filter(scope)
    )
    return [r.model_dump() for r in results]


class SessionEndRequest(BaseModel):
    session_id: str
    messages: List[SessionMessage]


@router.post("/session/end")
def session_end(body: SessionEndRequest, request: Request):
    user_id = get_user(request).user_id

    try:
        enqueue(
            user_id=user_id,
            kind="session_end",
            payload={
                "session_id": body.session_id,
                "messages": [m.model_dump() for m in body.messages],
            },
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    return {"status": "session end queued", "session_id": body.session_id}


class EntityExtractRequest(BaseModel):
    text: str
    evidence_id: str
    evidence_type: str = "SESSION"


@router.post("/entities/extract")
def entities_extract(body: EntityExtractRequest, request: Request):
    """Extract entities from arbitrary text and store them asynchronously.

    evidence_type must be SESSION or DOCUMENT.
    evidence_id is the session UUID or file_path that serves as provenance.
    """
    user_id = get_user(request).user_id

    try:
        enqueue(
            user_id=user_id,
            kind="entities_extract",
            payload={
                "text": body.text,
                "evidence_id": body.evidence_id,
                "evidence_type": body.evidence_type,
            },
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    return {"status": "extraction queued", "evidence_id": body.evidence_id}


@router.get("/entities")
def list_entities(
    request: Request,
    type: Optional[str] = Query(
        default=None, description="Filter by entity_type (e.g. Person, ORG)"
    ),
    scope: Optional[str] = Query(
        default=None,
        regex="^(personal|shared|system)$",
        description="Narrow to one scope; default is household union.",
    ),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Return known entities, optionally filtered by type and/or scope.

    Default visibility = household union via :func:`visibility.visible_filter`
    (personal-mine + shared + system). The headline-test invariant
    applies: admins requesting `?scope=personal` see only their OWN
    personal entities, not cross-user personal data.

    Ordered by mention_count descending. Paginated via limit/offset.
    Used by the dashboard Entities panel.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)

    user = get_user(request)
    ms = config.get_metadata_store()
    where_clause, where_params = visible_filter(user, _coerce_scope_filter(scope))

    try:
        if type:
            rows = ms.fetch_all(
                "SELECT name, entity_type, mention_count, aliases, context_tags, scope "
                "FROM entities "
                f"WHERE {where_clause} AND upper(entity_type) = upper(%s) "
                "ORDER BY mention_count DESC "
                "LIMIT %s OFFSET %s",
                (*where_params, type, limit, offset),
            )
        else:
            rows = ms.fetch_all(
                "SELECT name, entity_type, mention_count, aliases, context_tags, scope "
                "FROM entities "
                f"WHERE {where_clause} "
                "ORDER BY mention_count DESC "
                "LIMIT %s OFFSET %s",
                (*where_params, limit, offset),
            )
    except Exception as exc:
        _log.warning("list_entities: DB query failed — %s", exc)
        return []

    return [
        {
            "name": r["name"],
            "entity_type": r["entity_type"],
            "mention_count": r["mention_count"],
            "aliases": r["aliases"],
            "context_tags": r["context_tags"],
            "scope": r.get("scope", "personal"),
        }
        for r in rows
    ]
