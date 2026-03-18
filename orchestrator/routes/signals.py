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
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import config
from models.signals import RelevanceProfile, SourceConfig

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
    positive: Optional[bool] = None   # explicit feedback
    event_type: Optional[str] = None  # implicit feedback


# ---------------------------------------------------------------------------
# POST /sources
# ---------------------------------------------------------------------------


@router.post("/sources")
def add_or_preview_source(body: SourceRequest):
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
                "default",
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
def list_sources():
    """Return active sources with scheduler job status."""
    try:
        ms = config.get_metadata_store()
        rows = ms.fetch_all(
            "SELECT id, name, source_type, url, category, active, poll_interval, "
            "last_polled_at, last_signal_at FROM sources ORDER BY name"
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

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
                "last_polled_at": row["last_polled_at"].isoformat() if row["last_polled_at"] else None,
                "last_signal_at": row["last_signal_at"].isoformat() if row["last_signal_at"] else None,
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
    limit: int = Query(20, ge=1, le=100),
):
    """Return recent signals with optional filters."""
    conditions = ["user_id = %s"]
    params: list = ["default"]

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
            f"entities, topics, importance_score, relevance_score, notified, created_at "
            f"FROM signals WHERE {where} ORDER BY created_at DESC LIMIT %s",
            tuple(params),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

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
            }
        )
    return {"signals": result, "total": len(result)}


# ---------------------------------------------------------------------------
# PUT /profile
# ---------------------------------------------------------------------------


@router.put("/profile")
def upsert_profile(body: ProfileRequest):
    """Create or update the relevance profile for the default user."""
    try:
        ms = config.get_metadata_store()
        existing = ms.fetch_one(
            "SELECT id FROM relevance_profiles WHERE user_id = %s", ("default",)
        )
        if existing:
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
                    "default",
                ),
            )
            profile_id = str(existing["id"])
        else:
            profile_id = str(uuid.uuid4())
            ms.execute(
                "INSERT INTO relevance_profiles "
                "(id, user_id, tracked_locations, tracked_topics, tracked_entities, tracked_keywords) "
                "VALUES (%s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)",
                (
                    profile_id,
                    "default",
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
def get_profile():
    """Return the current relevance profile."""
    try:
        ms = config.get_metadata_store()
        row = ms.fetch_one(
            "SELECT id, tracked_locations, tracked_topics, tracked_entities, "
            "tracked_keywords, updated_at FROM relevance_profiles "
            "WHERE user_id = %s ORDER BY updated_at DESC LIMIT 1",
            ("default",),
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
def record_feedback(body: FeedbackRequest):
    """Record explicit (positive=true/false) or implicit (event_type=...) feedback."""
    from services.feedback import record_explicit, record_implicit

    if body.positive is not None:
        record_explicit(
            item_type=body.item_type,
            item_id=body.item_id,
            positive=body.positive,
        )
        return {"status": "ok", "type": "explicit", "positive": body.positive}

    if body.event_type:
        record_implicit(
            item_type=body.item_type,
            item_id=body.item_id,
            event_type=body.event_type,
        )
        return {"status": "ok", "type": "implicit", "event_type": body.event_type}

    raise HTTPException(
        status_code=422,
        detail="Provide either 'positive' (bool) for explicit feedback or 'event_type' (str) for implicit.",
    )


# ---------------------------------------------------------------------------
# Source detection helper
# ---------------------------------------------------------------------------


def _detect_source(url: str) -> dict:
    """Try to detect the source type and return up to 3 preview items.

    Detection order:
      1. Try RSS/Atom auto-detection (feedparser + link tag scanning).
      2. Fall back to page scraping via trafilatura.
    """
    from adapters.rss_source import RSSSource

    feed_url, preview_items = RSSSource.detect(url)
    if feed_url and preview_items:
        return {"source_type": "rss", "feed_url": feed_url, "preview_items": preview_items}

    # Fall back to page scraping.
    from adapters.page_scraper import PageScraper

    page_items = PageScraper.detect(url)
    return {
        "source_type": "page",
        "feed_url": None,
        "preview_items": page_items,
    }


def _infer_name(url: str) -> str:
    """Extract a readable source name from a URL."""
    from urllib.parse import urlparse

    host = urlparse(url).netloc or url
    return host.replace("www.", "").split(".")[0].capitalize()
