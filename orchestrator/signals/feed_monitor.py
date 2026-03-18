"""Feed monitor: polls RSS/Atom/JSON feeds and static pages on a schedule.

On start():
  - Loads all active sources from Postgres.
  - Schedules one APScheduler IntervalTrigger job per source, named
    "signal_poll_{source_id}", at source.poll_interval seconds.

schedule_source(source) is also called from routes/signals.py when a new
source is added via POST /sources?confirm=true, so it can start polling
immediately without a restart.
"""

import logging
from datetime import datetime, timezone

import config
from models.signals import SourceConfig
from services.signal_processor import process_signal

_log = logging.getLogger(__name__)


def start() -> None:
    """Load active sources from Postgres and schedule poll jobs."""
    scheduler = config.get_scheduler()
    if not scheduler.running:
        _log.info("feed_monitor: scheduler not running yet, skipping source load")
        return

    try:
        ms = config.get_metadata_store()
        rows = ms.fetch_all(
            "SELECT id, name, source_type, url, category, active, poll_interval, "
            "extraction_method, css_selector_override, last_polled_at, last_signal_at, "
            "user_id FROM sources WHERE active = TRUE"
        )
    except Exception as exc:
        _log.warning("feed_monitor: could not load sources from Postgres: %s", exc)
        return

    for row in rows:
        source = _row_to_source(row)
        schedule_source(source)

    _log.info("feed_monitor: scheduled %d source poll jobs", len(rows))


def stop() -> None:
    """Remove all signal_poll_* jobs from the scheduler."""
    try:
        scheduler = config.get_scheduler()
        for job in scheduler.get_jobs():
            if job.id.startswith("signal_poll_"):
                job.remove()
        _log.info("feed_monitor: removed all poll jobs")
    except Exception as exc:
        _log.warning("feed_monitor stop error: %s", exc)


def schedule_source(source: SourceConfig) -> None:
    """Add or replace the poll job for a single source."""
    scheduler = config.get_scheduler()
    job_id = f"signal_poll_{source.id}"

    existing = scheduler.get_job(job_id)
    if existing:
        existing.remove()

    scheduler.add_job(
        _poll_source,
        trigger="interval",
        seconds=max(60, source.poll_interval),
        args=[source],
        id=job_id,
        name=f"Poll {source.name} ({source.source_type})",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _log.info("Scheduled poll job %s every %ds", job_id, source.poll_interval)


def _poll_source(source: SourceConfig) -> None:
    """Job callback: fetch signals from one source and process them."""
    _log.info("Polling source: %s (%s)", source.name, source.source_type)

    adapter = _build_adapter(source)
    if adapter is None:
        return

    try:
        raw_signals = adapter.poll()
    except Exception as exc:
        _log.error("Poll error for source %s: %s", source.id, exc)
        raw_signals = []

    new_count = 0
    for raw in raw_signals:
        if _is_duplicate(raw.url, source.user_id):
            continue
        try:
            process_signal(raw, user_id=source.user_id)
            new_count += 1
        except Exception as exc:
            _log.error("signal_processor error for %r: %s", raw.title[:60], exc)

    _update_poll_timestamp(source)
    _log.info("Polled %s: %d new signals (of %d fetched)", source.name, new_count, len(raw_signals))


def _build_adapter(source: SourceConfig):
    """Instantiate the appropriate adapter for source.source_type."""
    try:
        if source.source_type == "rss":
            from adapters.rss_source import RSSSource
            return RSSSource(source)
        if source.source_type == "page":
            from adapters.page_scraper import PageScraper
            return PageScraper(source)
        if source.source_type == "playwright":
            from adapters.playwright_fetcher import PlaywrightFetcher
            return PlaywrightFetcher(source)
        if source.source_type == "caldav":
            from adapters.calendar_adapter import CalendarAdapter
            return CalendarAdapter(source)
        _log.warning("Unknown source_type %r for source %s", source.source_type, source.id)
    except Exception as exc:
        _log.error("Adapter init error for source %s: %s", source.id, exc)
    return None


def _is_duplicate(url: str, user_id: str) -> bool:
    """Check if a signal with this URL already exists for this user."""
    if not url:
        return False
    try:
        ms = config.get_metadata_store()
        row = ms.fetch_one(
            "SELECT 1 FROM signals WHERE url = %s AND user_id = %s LIMIT 1",
            (url, user_id),
        )
        return row is not None
    except Exception:
        return False


def _update_poll_timestamp(source: SourceConfig) -> None:
    try:
        ms = config.get_metadata_store()
        ms.execute(
            "UPDATE sources SET last_polled_at = %s WHERE id = %s",
            (datetime.now(timezone.utc), source.id),
        )
    except Exception as exc:
        _log.debug("Could not update last_polled_at for %s: %s", source.id, exc)


def _row_to_source(row: dict) -> SourceConfig:
    return SourceConfig(
        id=str(row["id"]),
        name=row["name"],
        source_type=row["source_type"],
        url=row["url"],
        category=row.get("category", ""),
        active=row["active"],
        poll_interval=row.get("poll_interval", 3600),
        extraction_method=row.get("extraction_method", "feedparser"),
        css_selector_override=row.get("css_selector_override"),
        last_polled_at=row.get("last_polled_at"),
        last_signal_at=row.get("last_signal_at"),
        user_id=row.get("user_id", "default"),
    )
