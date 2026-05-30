# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
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
from dataclasses import replace
from datetime import datetime
from datetime import timezone

from models.signals import SourceConfig
from services.signal_processor import process_signal

import config

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
            "poll_cursor, user_id FROM sources WHERE active = TRUE"
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
    """Dispatcher: paperless REST ingest vs RSS-style signal adapters."""
    if source.source_type == "paperless":
        _poll_paperless_source(source)
    else:
        _poll_signal_source(source)


def _poll_signal_source(source: SourceConfig) -> None:
    """RSS/page/playwright/caldav path: adapter poll → dedup → process_signal."""
    _log.info("Polling source: %s (%s)", source.name, source.source_type)

    adapter = _build_adapter(source)
    if adapter is None:
        return

    try:
        raw_signals = adapter.poll()
    except Exception as exc:
        _log.error(
            "Poll error for source %s: type=%s msg=%s",
            source.id,
            type(exc).__name__,
            str(exc)[:200],
        )
        raw_signals = []

    new_count = 0
    for raw in raw_signals:
        if _is_duplicate(raw.url, source.user_id):
            continue
        try:
            process_signal(raw, user_id=source.user_id)
            new_count += 1
        except Exception as exc:
            _log.error(
                "signal_processor error for %r: type=%s msg=%s",
                raw.title[:60],
                type(exc).__name__,
                str(exc)[:200],
            )

    _update_poll_timestamp(source)
    _log.info("Polled %s: %d new signals (of %d fetched)", source.name, new_count, len(raw_signals))


_MAX_PAPERLESS_DOCS_PER_TICK = 500
_MAX_PAPERLESS_CHUNKS_PER_TICK = 2000


def _fetch_poll_cursor(source_id: str) -> str | None:
    try:
        ms = config.get_metadata_store()
        row = ms.fetch_one("SELECT poll_cursor FROM sources WHERE id = %s", (source_id,))
    except Exception:
        return None
    if not row:
        return None
    v = row.get("poll_cursor")
    if v is None or v == "":
        return None
    return str(v)


def _poll_paperless_source(source: SourceConfig) -> None:
    """paperless-ngx incremental ingest — never calls ``process_signal``."""
    _log.info("Polling paperless source: %s (%s)", source.name, source.id)
    from adapters.paperless_source import PaperlessPoller
    from services.ingest import chunk_text
    from services.ingest import ingest_external_document

    from services import paperless_credentials as pwc

    cursor = _fetch_poll_cursor(source.id)
    source = replace(source, poll_cursor=cursor)
    # Paperless uses ``added__gt=<since_cursor>`` for incremental fetch. That
    # filter is **strict** on ``added``. If we advance ``since_cursor`` between
    # HTTP pages (after each ingested row), page N+1 can drop every document that
    # still shares the same ``added`` timestamp as an earlier page — a silent
    # skip when bulk imports stamp identical timestamps. Keep one watermark
    # for all pages in this poll tick; ``ingest_external_document`` still
    # advances ``sources.poll_cursor`` in Postgres per document.
    fetch_since = cursor

    try:
        conn = pwc.load_connection(source.user_id)
    except Exception as exc:
        _log.warning(
            "paperless_poll_skip connector=%s code=credential user_id=%s type=%s msg=%s",
            "paperless",
            source.user_id,
            type(exc).__name__,
            str(exc)[:200],
        )
        _update_poll_timestamp(source)
        return

    cred_base = conn.base_url.rstrip("/")
    src_url = (source.url or "").rstrip("/")
    if cred_base != src_url:
        _log.warning(
            "paperless_url_mismatch source_id=%s credential_base=%r sources_url=%r",
            source.id,
            cred_base,
            src_url,
        )

    poller = PaperlessPoller(conn)
    docs_done = 0
    chunks_used = 0
    page = 1

    while docs_done < _MAX_PAPERLESS_DOCS_PER_TICK and chunks_used < _MAX_PAPERLESS_CHUNKS_PER_TICK:
        docs, auth_fail = poller.fetch_documents_page(since_cursor=fetch_since, page=page)
        if auth_fail:
            _log.warning("paperless_auth_failure source_id=%s", source.id)
            break
        if not docs:
            break

        hard_stop = False
        for doc in docs:
            at_doc_cap = docs_done >= _MAX_PAPERLESS_DOCS_PER_TICK
            at_chunk_cap = chunks_used >= _MAX_PAPERLESS_CHUNKS_PER_TICK
            if at_doc_cap or at_chunk_cap:
                hard_stop = True
                break

            stored = _fetch_poll_cursor(source.id)
            if stored and doc.added < stored:
                _log.warning(
                    "paperless_added_before_cursor source_id=%s doc_added=%r stored=%r",
                    source.id,
                    doc.added,
                    stored,
                )
                docs_done += 1
                continue

            ext_id = str(int(doc.id))
            try:
                est_chunks = len(chunk_text(doc.content))
            except Exception:
                est_chunks = 1
            if chunks_used + est_chunks > _MAX_PAPERLESS_CHUNKS_PER_TICK:
                hard_stop = True
                break

            try:
                result = ingest_external_document(
                    user_id=source.user_id,
                    source_id=str(source.id),
                    external_kind="paperless",
                    external_document_id=ext_id,
                    content=doc.content,
                    poll_watermark=doc.added,
                    stored_source_poll_cursor=stored,
                )
            except Exception as exc:
                _log.error(
                    "paperless_ingest_failed source_id=%s doc_id=%s type=%s msg=%s",
                    source.id,
                    ext_id,
                    type(exc).__name__,
                    str(exc)[:200],
                )
                hard_stop = True
                break

            chunks_used += int(result.chunk_count or 0)
            # Blocked-high external ingest returns ``advance_external_poll_cursor=False``
            # without ``skipped=True`` (see ``ingest_external_document``). If we kept
            # polling, a later row could monotonically advance ``poll_cursor`` past this
            # document's ``added`` while it was never reconciled — strict ``added__gt``
            # would then drop it permanently.
            if not result.advance_external_poll_cursor and not result.skipped:
                hard_stop = True
                break

            docs_done += 1

        if hard_stop:
            break
        page += 1

    _update_poll_timestamp(source)
    _log.info(
        "paperless_poll_done source_id=%s docs_touched=%s chunks_est=%s",
        source.id,
        docs_done,
        chunks_used,
    )


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
    pc = row.get("poll_cursor")
    if pc == "":
        pc = None
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
        poll_cursor=pc,
        user_id=row.get("user_id", "default"),
    )
