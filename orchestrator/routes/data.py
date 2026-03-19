# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""Data endpoints: /ingest, /search, /session/end, /entities/extract, /entities."""

from typing import List, Optional

import config
from auth import get_user
from fastapi import APIRouter, BackgroundTasks, Query, Request
from pydantic import BaseModel
from services.ingest import ingest_folder
from services.search import semantic_search

router = APIRouter()


class IngestRequest(BaseModel):
    path: str = "/data"


@router.post("/ingest")
def ingest_endpoint(body: IngestRequest, bg: BackgroundTasks):
    bg.add_task(ingest_folder, body.path)
    return {"status": "ingest started", "path": body.path}


@router.get("/search")
def search_endpoint(q: str, limit: int = 5, request: Request = None):
    user_id = get_user(request).user_id if request else "default"
    results = semantic_search(q, limit=limit, user_id=user_id)
    return [r.model_dump() for r in results]


class SessionMessage(BaseModel):
    role: str
    content: str


class SessionEndRequest(BaseModel):
    session_id: str
    messages: List[SessionMessage]


@router.post("/session/end")
def session_end(body: SessionEndRequest, bg: BackgroundTasks, request: Request):
    user_id = get_user(request).user_id

    def _process_session():
        from services.entities import extract_entities, store_entities
        from services.memory import store_session, summarize_session

        msg_dicts = [{"role": m.role, "content": m.content} for m in body.messages]
        summary = summarize_session(msg_dicts, session_id=body.session_id)
        store_session(summary, user_id=user_id)

        session_text = "\n".join(
            f"{m['role']}: {m['content']}" for m in msg_dicts
        )
        entities = extract_entities(session_text)
        store_entities(
            entities,
            evidence_id=body.session_id,
            evidence_type="SESSION",
            user_id=user_id,
        )

    bg.add_task(_process_session)
    return {"status": "session end processing", "session_id": body.session_id}


class EntityExtractRequest(BaseModel):
    text: str
    evidence_id: str
    evidence_type: str = "SESSION"


@router.post("/entities/extract")
def entities_extract(body: EntityExtractRequest, bg: BackgroundTasks, request: Request):
    """Extract entities from arbitrary text and store them asynchronously.

    evidence_type must be SESSION or DOCUMENT.
    evidence_id is the session UUID or file_path that serves as provenance.
    """
    user_id = get_user(request).user_id

    def _run():
        from services.entities import extract_entities, store_entities

        entities = extract_entities(body.text)
        store_entities(
            entities,
            evidence_id=body.evidence_id,
            evidence_type=body.evidence_type,
            user_id=user_id,
        )

    bg.add_task(_run)
    return {"status": "extraction started", "evidence_id": body.evidence_id}


@router.get("/entities")
def list_entities(
    request: Request,
    type: Optional[str] = Query(default=None, description="Filter by entity_type (e.g. Person, ORG)"),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Return known entities, optionally filtered by type.

    Ordered by mention_count descending. Paginated via limit/offset.
    Used by the dashboard Entities panel.
    """
    user_id = get_user(request).user_id
    ms = config.get_metadata_store()

    if type:
        rows = ms.fetch_all(
            "SELECT name, entity_type, mention_count, aliases, context_tags "
            "FROM entities "
            "WHERE user_id = %s AND upper(entity_type) = upper(%s) "
            "ORDER BY mention_count DESC "
            "LIMIT %s OFFSET %s",
            (user_id, type, limit, offset),
        )
    else:
        rows = ms.fetch_all(
            "SELECT name, entity_type, mention_count, aliases, context_tags "
            "FROM entities "
            "WHERE user_id = %s "
            "ORDER BY mention_count DESC "
            "LIMIT %s OFFSET %s",
            (user_id, limit, offset),
        )

    return [
        {
            "name": r["name"],
            "entity_type": r["entity_type"],
            "mention_count": r["mention_count"],
            "aliases": r["aliases"],
            "context_tags": r["context_tags"],
        }
        for r in rows
    ]
