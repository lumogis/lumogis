"""Page monitor: detects content changes on static web pages.

Stores a SHA-256 content hash per URL in memory (keyed by source_id).
On each poll:
  - Fetches the page via page_scraper.
  - Compares hash to stored value.
  - On change: creates a Signal with the new content, passes to signal_processor.

Poll interval is per-source (source.poll_interval) or falls back to
SIGNAL_POLL_INTERVAL_SCRAPE env var (default 3600s).
"""

import hashlib
import logging
import os
import uuid
from datetime import datetime, timezone

import config
from adapters.page_scraper import PageScraper
from models.signals import Signal, SourceConfig
from services.signal_processor import process_signal

_log = logging.getLogger(__name__)

_DEFAULT_INTERVAL = int(os.environ.get("SIGNAL_POLL_INTERVAL_SCRAPE", "3600"))

# In-memory hash store: source_id -> sha256 hex digest of last known content.
_content_hashes: dict[str, str] = {}

_job_id = "page_monitor_poll"


def start() -> None:
    """Schedule page monitor poll job for all active 'page' and 'playwright' sources."""
    scheduler = config.get_scheduler()
    if not scheduler.running:
        return

    scheduler.add_job(
        _poll_all_pages,
        trigger="interval",
        seconds=_DEFAULT_INTERVAL,
        id=_job_id,
        name="Page content change monitor",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _log.info("page_monitor: scheduled poll every %ds", _DEFAULT_INTERVAL)


def stop() -> None:
    try:
        scheduler = config.get_scheduler()
        job = scheduler.get_job(_job_id)
        if job:
            job.remove()
    except Exception as exc:
        _log.debug("page_monitor stop: %s", exc)


def _poll_all_pages() -> None:
    """Fetch all active page/playwright sources and check for changes."""
    try:
        ms = config.get_metadata_store()
        rows = ms.fetch_all(
            "SELECT id, name, source_type, url, category, active, poll_interval, "
            "extraction_method, css_selector_override, last_polled_at, last_signal_at, "
            "user_id FROM sources WHERE active = TRUE AND source_type IN ('page', 'playwright')"
        )
    except Exception as exc:
        _log.warning("page_monitor: DB error loading sources: %s", exc)
        return

    for row in rows:
        source = _row_to_source(row)
        _check_page(source)


def _check_page(source: SourceConfig) -> None:
    """Compare current content hash against stored; emit Signal on change."""
    scraper = PageScraper(source)
    signals = scraper.poll()
    if not signals:
        return

    raw = signals[0]
    content_hash = hashlib.sha256(raw.raw_content.encode()).hexdigest()
    stored_hash = _content_hashes.get(source.id)

    if stored_hash is None:
        # First run — store hash, don't emit a signal.
        _content_hashes[source.id] = content_hash
        _log.debug("page_monitor: baseline hash stored for %s", source.url)
        return

    if content_hash == stored_hash:
        _log.debug("page_monitor: no change for %s", source.url)
        return

    _log.info("page_monitor: content changed for %s — creating signal", source.url)
    _content_hashes[source.id] = content_hash

    change_signal = Signal(
        signal_id=str(uuid.uuid4()),
        source_id=source.id,
        title=f"Page updated: {raw.title}",
        url=source.url,
        published_at=datetime.now(timezone.utc),
        content_summary="",
        raw_content=raw.raw_content,
        entities=[],
        topics=["page-change"],
        importance_score=0.0,
        relevance_score=0.0,
        notified=False,
        created_at=datetime.now(timezone.utc),
        user_id=source.user_id,
    )
    try:
        process_signal(change_signal, user_id=source.user_id)
    except Exception as exc:
        _log.error("page_monitor: signal_processor error for %s: %s", source.url, exc)


def _row_to_source(row: dict) -> SourceConfig:
    return SourceConfig(
        id=str(row["id"]),
        name=row["name"],
        source_type=row["source_type"],
        url=row["url"],
        category=row.get("category", ""),
        active=row["active"],
        poll_interval=row.get("poll_interval", _DEFAULT_INTERVAL),
        extraction_method=row.get("extraction_method", "trafilatura"),
        css_selector_override=row.get("css_selector_override"),
        last_polled_at=row.get("last_polled_at"),
        last_signal_at=row.get("last_signal_at"),
        user_id=row.get("user_id", "default"),
    )
