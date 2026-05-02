# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Memory / search endpoints for the v1 façade.

Wraps :func:`services.search.semantic_search` and
:func:`services.memory.recent_sessions` with stable wire DTOs. Visibility
is delegated to those helpers (household scope union by default).

Degradation contract (plan §Error handling):

* If ``app.state.embedding_ready`` is ``False`` → return
  ``MemorySearchResponse(hits=[], degraded=True, reason="embedder_not_ready")``
  with HTTP 200. The SPA renders a banner; nothing 5xx-bubbles.
* If the vector store raises → return ``hits=[], degraded=True,
  reason="vector_store_unavailable"`` and emit a ``Warning: 199`` HTTP
  header so reverse-proxy log scrapers can spot soft failures.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from auth import get_user
from authz import require_user
from models.api_v1 import (
    MemorySearchHit,
    MemorySearchResponse,
    RecentSession,
    RecentSessionsResponse,
)

_log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/memory",
    tags=["v1-memory"],
    dependencies=[Depends(require_user)],
)


def _embedder_ready(request: Request) -> bool:
    """``app.state.embedding_ready`` is set by the lifespan startup probe."""
    state = getattr(request.app, "state", None)
    if state is None:
        return True
    return bool(getattr(state, "embedding_ready", True))


@router.get("/search", response_model=MemorySearchResponse)
def search(
    request: Request,
    q: str = Query(..., min_length=1, max_length=512),
    limit: int = Query(10, ge=1, le=50),
) -> MemorySearchResponse | JSONResponse:
    user_id = get_user(request).user_id

    if not _embedder_ready(request):
        return MemorySearchResponse(
            hits=[],
            degraded=True,
            reason="embedder_not_ready",
        )

    from services.search import semantic_search

    try:
        results = semantic_search(q, limit=limit, user_id=user_id)
    except Exception:  # noqa: BLE001 — soft-fail per plan §Error handling
        _log.warning("memory.search: vector_store unavailable", exc_info=True)
        body = MemorySearchResponse(
            hits=[],
            degraded=True,
            reason="vector_store_unavailable",
        )
        return JSONResponse(
            content=body.model_dump(mode="json"),
            headers={"Warning": '199 - "vector_store unavailable"'},
        )

    hits: list[MemorySearchHit] = []
    for r in results:
        meta = getattr(r, "metadata", None) or {}
        hits.append(
            MemorySearchHit(
                id=str(getattr(r, "file_path", meta.get("id", ""))),
                score=float(getattr(r, "score", 0.0)),
                title=meta.get("title"),
                snippet=getattr(r, "chunk_text", "")[:2_000],
                source=meta.get("source"),
                created_at=_coerce_datetime(meta.get("created_at")),
                scope=meta.get("scope", "personal"),
                owner_user_id=meta.get("owner_user_id"),
            )
        )
    return MemorySearchResponse(hits=hits)


@router.get("/recent", response_model=RecentSessionsResponse)
def recent(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
) -> RecentSessionsResponse:
    user_id = get_user(request).user_id

    from services.memory import recent_sessions

    sessions = recent_sessions(limit=limit, user_id=user_id)

    out: list[RecentSession] = []
    for s in sessions:
        ended = _resolve_ended_at(s)
        if ended is None:
            continue
        out.append(
            RecentSession(
                session_id=str(s.session_id),
                summary=s.summary or "",
                ended_at=ended,
            )
        )
    return RecentSessionsResponse(sessions=out)


def _coerce_datetime(value: Optional[object]):
    """Best-effort datetime parse — accepts ISO strings or pre-parsed datetimes."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):  # datetime / date
        return value
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _resolve_ended_at(session) -> Optional[object]:
    """Pull a wall-clock timestamp off a :class:`SessionSummary`-shaped object.

    The shipped :class:`services.memory.SessionSummary` does not expose
    ``ended_at`` directly — the underlying Postgres row has ``updated_at``,
    which the helper drops. Until a follow-up promotes the column, we
    fall back to ``getattr(s, 'updated_at', None)`` so the wire field is
    populated when the helper is extended without changing the route.
    """
    for attr in ("ended_at", "updated_at", "created_at"):
        value = getattr(session, attr, None)
        if value is not None:
            return value
    return None
