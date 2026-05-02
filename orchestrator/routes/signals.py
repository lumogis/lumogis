# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Signal infrastructure routes.

POST /sources       — Two-step stateless source addition:
                      confirm=false: detect feed type + preview (no save)
                      confirm=true:  re-detect, save, start polling
GET  /sources       — Active sources with scheduler poll status
GET  /signals       — Recent signals, filterable
PUT  /profile       — Upsert relevance profile
GET  /profile       — Return current profile
POST /feedback      — Record explicit or implicit feedback
"""

import json
import logging
import uuid
from typing import Optional

from auth import UserContext
from authz import require_admin
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from models.signals import SourceConfig
from pydantic import BaseModel
from services.signal_source_detection import detect_signal_source
from visibility import visible_filter

import config

_log = logging.getLogger(__name__)

router = APIRouter(tags=["signals"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class SourceRequest(BaseModel):
    url: str
    confirm: bool = False
    name: Optional[str] = None
    category: Optional[str] = "general"
    poll_interval: Optional[int] = 3600


class ProfileRequest(BaseModel):
    tracked_locations: list[str] = []
    tracked_topics: list[str] = []
    tracked_entities: list[str] = []
    tracked_keywords: list[str] = []


class FeedbackRequest(BaseModel):
    item_type: str
    item_id: str
    positive: Optional[bool] = None  # explicit feedback
    event_type: Optional[str] = None  # implicit feedback


# ---------------------------------------------------------------------------
# POST /sources
# ---------------------------------------------------------------------------


@router.post("/sources")
def add_or_preview_source(
    body: SourceRequest,
    user: UserContext = Depends(require_admin),
):
    """Two-step stateless source flow.

    confirm=false → detect + preview (no DB write).
    confirm=true  → detect + save + schedule polling.
    """
    detection = _detect_source(body.url)

    if not body.confirm:
        return {
            "source_type": detection["source_type"],
            "url": detection["feed_url"] or body.url,
            "preview_items": detection["preview_items"],
        }

    # confirm=true — save and start polling.
    source_id = str(uuid.uuid4())
    source_name = body.name or _infer_name(body.url)
    feed_url = detection["feed_url"] or body.url
    source_type = detection["source_type"]
    extraction_method = "feedparser" if source_type == "rss" else "trafilatura"

    try:
        ms = config.get_metadata_store()
        ms.execute(
            "INSERT INTO sources "
            "(id, user_id, name, source_type, url, category, active, poll_interval, "
            "extraction_method, css_selector_override) "
            "VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s, %s, NULL)",
            (
                source_id,
                user.user_id,
                source_name,
                source_type,
                feed_url,
                body.category or "general",
                body.poll_interval or 3600,
                extraction_method,
            ),
        )
    except Exception as exc:
        _log.error("Failed to save source %s: %s", body.url, exc)
        raise HTTPException(status_code=500, detail=f"Could not save source: {exc}")

    source = SourceConfig(
        id=source_id,
        name=source_name,
        source_type=source_type,
        url=feed_url,
        category=body.category or "general",
        active=True,
        poll_interval=body.poll_interval or 3600,
        extraction_method=extraction_method,
        css_selector_override=None,
        last_polled_at=None,
        last_signal_at=None,
        user_id=user.user_id,
    )

    try:
        from signals.feed_monitor import schedule_source

        schedule_source(source)
    except Exception as exc:
        _log.warning("Could not schedule poll job for %s: %s", source_id, exc)

    return {
        "status": "created",
        "source_id": source_id,
        "source_type": source_type,
        "url": feed_url,
        "preview_items": detection["preview_items"],
    }


# ---------------------------------------------------------------------------
# GET /sources
# ---------------------------------------------------------------------------


@router.get("/sources")
def list_sources(user: UserContext = Depends(require_user)):
    """Return the calling user's active sources with scheduler job status."""
    try:
        ms = config.get_metadata_store()
        # SCOPE-EXEMPT: `sources` is in plan §2.10's excluded-from-scope
        # list — sources are per-user signal-polling config, not memory
        # content; no `scope` column exists.
        rows = ms.fetch_all(
            "SELECT id, name, source_type, url, category, active, poll_interval, "
            "last_polled_at, last_signal_at FROM sources "
            "WHERE user_id = %s ORDER BY name",
            (user.user_id,),
        )
    except Exception as exc:
        _log.warning("list_sources: DB query failed — %s", exc)
        return {"sources": [], "total": 0}

    scheduler = config.get_scheduler()
    scheduled_ids = {job.id for job in scheduler.get_jobs()}

    result = []
    for row in rows:
        job_id = f"signal_poll_{row['id']}"
        result.append(
            {
                "id": str(row["id"]),
                "name": row["name"],
                "source_type": row["source_type"],
                "url": row["url"],
                "category": row["category"],
                "active": row["active"],
                "poll_interval": row["poll_interval"],
                "last_polled_at": row["last_polled_at"].isoformat()
                if row["last_polled_at"]
                else None,
                "last_signal_at": row["last_signal_at"].isoformat()
                if row["last_signal_at"]
                else None,
                "polling_active": job_id in scheduled_ids,
            }
        )
    return {"sources": result, "total": len(result)}


# ---------------------------------------------------------------------------
# GET /signals
# ---------------------------------------------------------------------------


@router.get("/signals")
def list_signals(
    topic: Optional[str] = Query(None),
    entity: Optional[str] = Query(None),
    source_id: Optional[str] = Query(None),
    min_relevance_score: Optional[float] = Query(None),
    scope: Optional[str] = Query(
        None,
        regex="^(personal|shared|system)$",
        description="Narrow to one scope; default is household union.",
    ),
    limit: int = Query(20, ge=1, le=100),
    user: UserContext = Depends(require_user),
):
    """Return recent signals visible to the caller (household union by default).

    The default visibility surface is the household union via
    :func:`visibility.visible_filter`:
    `(scope='personal' AND user_id=$me) OR scope IN ('shared','system')`.
    System signals (`source_id='__system__'`, `scope='system'`) are
    therefore visible to every user — that is the contract; the dashboard
    System panel relies on this.

    Selecting `?scope=personal` returns only the caller's own personal
    signals (admins included — see the headline-test invariant in
    plan §2.8). `source_url` and `source_label` are denormalized at
    write time so shared/system signals render without joining
    `sources`.
    """
    conditions: list[str] = []
    params: list = []

    where_clause, where_params = visible_filter(user, scope)
    conditions.append(where_clause)
    params.extend(where_params)

    if topic:
        conditions.append("topics::text ILIKE %s")
        params.append(f"%{topic}%")
    if entity:
        conditions.append("entities::text ILIKE %s")
        params.append(f"%{entity}%")
    if source_id:
        conditions.append("source_id = %s")
        params.append(source_id)
    if min_relevance_score is not None:
        conditions.append("relevance_score >= %s")
        params.append(min_relevance_score)

    where = " AND ".join(conditions)
    params.append(limit)

    try:
        ms = config.get_metadata_store()
        rows = ms.fetch_all(
            f"SELECT signal_id, source_id, title, url, published_at, content_summary, "
            f"entities, topics, importance_score, relevance_score, notified, created_at, "
            f"scope, source_url, source_label "
            f"FROM signals WHERE {where} ORDER BY created_at DESC LIMIT %s",
            tuple(params),
        )
    except Exception as exc:
        _log.warning("list_signals: DB query failed — %s", exc)
        return {"signals": [], "total": 0}

    result = []
    for row in rows:
        result.append(
            {
                "signal_id": str(row["signal_id"]),
                "source_id": str(row["source_id"]),
                "title": row["title"],
                "url": row["url"],
                "published_at": row["published_at"].isoformat() if row["published_at"] else None,
                "content_summary": row["content_summary"],
                "entities": row["entities"] or [],
                "topics": row["topics"] or [],
                "importance_score": float(row["importance_score"]),
                "relevance_score": float(row["relevance_score"]),
                "notified": row["notified"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "scope": row.get("scope", "personal"),
                "source_url": row.get("source_url"),
                "source_label": row.get("source_label"),
            }
        )
    return {"signals": result, "total": len(result)}


# ---------------------------------------------------------------------------
# PUT /profile
# ---------------------------------------------------------------------------


@router.put("/profile")
def upsert_profile(
    body: ProfileRequest,
    user: UserContext = Depends(require_user),
):
    """Create or update the relevance profile for the calling user."""
    try:
        ms = config.get_metadata_store()
        # SCOPE-EXEMPT: `relevance_profiles` is in plan §2.10's
        # excluded-from-scope list — relevance is a per-user signal-routing
        # config, not memory content; no `scope` column exists.
        existing = ms.fetch_one(
            "SELECT id FROM relevance_profiles WHERE user_id = %s", (user.user_id,)
        )
        if existing:
            # SCOPE-EXEMPT: see above.
            ms.execute(
                "UPDATE relevance_profiles SET "
                "tracked_locations = %s::jsonb, tracked_topics = %s::jsonb, "
                "tracked_entities = %s::jsonb, tracked_keywords = %s::jsonb, "
                "updated_at = NOW() WHERE user_id = %s",
                (
                    json.dumps(body.tracked_locations),
                    json.dumps(body.tracked_topics),
                    json.dumps(body.tracked_entities),
                    json.dumps(body.tracked_keywords),
                    user.user_id,
                ),
            )
            profile_id = str(existing["id"])
        else:
            profile_id = str(uuid.uuid4())
            ms.execute(
                "INSERT INTO relevance_profiles "
                "(id, user_id, tracked_locations, tracked_topics, "
                "tracked_entities, tracked_keywords) "
                "VALUES (%s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)",
                (
                    profile_id,
                    user.user_id,
                    json.dumps(body.tracked_locations),
                    json.dumps(body.tracked_topics),
                    json.dumps(body.tracked_entities),
                    json.dumps(body.tracked_keywords),
                ),
            )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {
        "status": "ok",
        "profile_id": profile_id,
        "tracked_locations": body.tracked_locations,
        "tracked_topics": body.tracked_topics,
        "tracked_entities": body.tracked_entities,
        "tracked_keywords": body.tracked_keywords,
    }


# ---------------------------------------------------------------------------
# GET /profile
# ---------------------------------------------------------------------------


@router.get("/profile")
def get_profile(user: UserContext = Depends(require_user)):
    """Return the calling user's current relevance profile."""
    try:
        ms = config.get_metadata_store()
        # SCOPE-EXEMPT: `relevance_profiles` is in plan §2.10's
        # excluded-from-scope list — per-user routing config.
        row = ms.fetch_one(
            "SELECT id, tracked_locations, tracked_topics, tracked_entities, "
            "tracked_keywords, updated_at FROM relevance_profiles "
            "WHERE user_id = %s ORDER BY updated_at DESC LIMIT 1",
            (user.user_id,),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if not row:
        return {
            "profile_id": None,
            "tracked_locations": [],
            "tracked_topics": [],
            "tracked_entities": [],
            "tracked_keywords": [],
            "updated_at": None,
        }

    return {
        "profile_id": str(row["id"]),
        "tracked_locations": row["tracked_locations"] or [],
        "tracked_topics": row["tracked_topics"] or [],
        "tracked_entities": row["tracked_entities"] or [],
        "tracked_keywords": row["tracked_keywords"] or [],
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


# ---------------------------------------------------------------------------
# POST /feedback
# ---------------------------------------------------------------------------


@router.post("/feedback")
def record_feedback(
    body: FeedbackRequest,
    user: UserContext = Depends(require_user),
):
    """Record explicit (positive=true/false) or implicit (event_type=...) feedback."""
    from services.feedback import record_explicit
    from services.feedback import record_implicit

    if body.positive is not None:
        record_explicit(
            item_type=body.item_type,
            item_id=body.item_id,
            positive=body.positive,
            user_id=user.user_id,
        )
        return {"status": "ok", "type": "explicit", "positive": body.positive}

    if body.event_type:
        record_implicit(
            item_type=body.item_type,
            item_id=body.item_id,
            event_type=body.event_type,
            user_id=user.user_id,
        )
        return {"status": "ok", "type": "implicit", "event_type": body.event_type}

    raise HTTPException(
        status_code=422,
        detail=(
            "Provide either 'positive' (bool) for explicit feedback "
            "or 'event_type' (str) for implicit."
        ),
    )


# ---------------------------------------------------------------------------
# Source detection helper
# ---------------------------------------------------------------------------


def _detect_source(url: str) -> dict:
    """Try to detect the source type and return up to 3 preview items.

    Detection order:
      1. Try RSS/Atom auto-detection (feedparser + link tag scanning).
      2. Fall back to page scraping via trafilatura.

    Implementation lives in :mod:`services.signal_source_detection` so routes
    do not import :mod:`adapters` (``architecture_import_boundary_tests``).
    """
    return detect_signal_source(url)


def _infer_name(url: str) -> str:
    """Extract a readable source name from a URL."""
    from urllib.parse import urlparse

    host = urlparse(url).netloc or url
    return host.replace("www.", "").split(".")[0].capitalize()
