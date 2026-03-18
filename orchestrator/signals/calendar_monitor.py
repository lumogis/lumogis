"""Calendar monitor: polls CalDAV for upcoming events.

Only active when CALENDAR_CALDAV_URL is set. Polls on a configurable interval
(CALENDAR_POLL_INTERVAL, default 1800s / 30 minutes).

Events starting within CALENDAR_LOOKAHEAD_HOURS (default 24h) are emitted
as Signal objects. Entity references from the known entities table are appended
when the event summary contains a known entity name.
"""

import logging
import os

import config
from adapters.calendar_adapter import CalendarAdapter
from models.signals import SourceConfig
from services.signal_processor import process_signal

_log = logging.getLogger(__name__)

_CALDAV_URL = os.environ.get("CALENDAR_CALDAV_URL", "")
_POLL_INTERVAL = int(os.environ.get("CALENDAR_POLL_INTERVAL", "1800"))
_job_id = "calendar_monitor_poll"


def start() -> None:
    if not _CALDAV_URL:
        _log.info("calendar_monitor: CALENDAR_CALDAV_URL not set — inactive")
        return

    scheduler = config.get_scheduler()
    if not scheduler.running:
        return

    scheduler.add_job(
        _poll_calendar,
        trigger="interval",
        seconds=_POLL_INTERVAL,
        id=_job_id,
        name="CalDAV calendar monitor",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _log.info("calendar_monitor: scheduled every %ds", _POLL_INTERVAL)


def stop() -> None:
    try:
        scheduler = config.get_scheduler()
        job = scheduler.get_job(_job_id)
        if job:
            job.remove()
    except Exception as exc:
        _log.debug("calendar_monitor stop: %s", exc)


def _poll_calendar() -> None:
    source = SourceConfig(
        id="__caldav__",
        name="CalDAV Calendar",
        source_type="caldav",
        url=_CALDAV_URL,
        category="calendar",
        active=True,
        poll_interval=_POLL_INTERVAL,
        extraction_method="caldav",
        css_selector_override=None,
        last_polled_at=None,
        last_signal_at=None,
    )
    adapter = CalendarAdapter(source)
    try:
        raw_signals = adapter.poll()
    except Exception as exc:
        _log.error("calendar_monitor: poll error: %s", exc)
        return

    for raw in raw_signals:
        raw.entities = _enrich_entities(raw.title + " " + raw.raw_content)
        try:
            process_signal(raw, user_id="default")
        except Exception as exc:
            _log.error("calendar_monitor: process_signal error: %s", exc)

    if raw_signals:
        _log.info("calendar_monitor: processed %d upcoming events", len(raw_signals))


def _enrich_entities(text: str) -> list[dict]:
    """Look up known entities that appear in the event text."""
    try:
        ms = config.get_metadata_store()
        rows = ms.fetch_all("SELECT name, entity_type FROM entities LIMIT 200")
        matched = []
        text_lower = text.lower()
        for row in rows:
            if row["name"].lower() in text_lower:
                matched.append({"name": row["name"], "type": row["entity_type"]})
        return matched
    except Exception:
        return []
