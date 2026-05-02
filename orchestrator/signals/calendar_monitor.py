# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Calendar monitor — legacy single-user CalDAV polling.

Status (per ``.cursor/plans/caldav_connector_credentials.plan.md``
D1 / D7):

* ``AUTH_ENABLED=false`` (single-user dev): unchanged. Reads
  ``CALENDAR_CALDAV_URL`` / ``CALENDAR_POLL_INTERVAL`` from the
  process environment and schedules an APScheduler job that polls
  on behalf of the ``"default"`` user. The legacy ``__caldav__`` job.
* ``AUTH_ENABLED=true`` (multi-user): **refuses to schedule.** The
  canonical multi-user CalDAV path is per-user ``sources`` rows
  driven by :mod:`signals.feed_monitor`; per-user credentials are
  resolved via :mod:`services.caldav_credentials`. A single
  operator-facing INFO log is emitted on first ``start()`` call
  pointing at the migration path.

Env-read discipline: ``CALENDAR_CALDAV_URL`` and
``CALENDAR_POLL_INTERVAL`` are read at *call time* inside
:func:`start` and :func:`_poll_calendar`, never at module import,
so test cases can ``monkeypatch.setenv`` between cases without
import gymnastics. APScheduler ``add_job(seconds=…)`` snapshots the
cadence at ``start()`` time; changes to ``CALENDAR_POLL_INTERVAL``
between polls do NOT retune the running job (a ``stop()`` + ``start()``
cycle is required). Per-poll URL / credential drift IS picked up
without a restart because :func:`_poll_calendar` re-reads on every
tick. This matches the pre-chunk behaviour and is documented here so
test fixtures can rely on it.
"""

import logging
import os

from adapters.calendar_adapter import CalendarAdapter
from auth import auth_enabled
from models.signals import SourceConfig
from services.signal_processor import process_signal

import config

_log = logging.getLogger(__name__)

_LEGACY_USER_ID = "default"
"""User-id under which the legacy env-driven job's signals are stored.

Locked by D10. Matches :data:`auth._DEV_USER_ID` so a future migration
to a real user row is a one-line change. The constant exists so
the two construction sites (``SourceConfig`` + ``process_signal``)
read from the same name and can never drift apart silently.
"""

_job_id = "calendar_monitor_poll"

_AUTH_DISABLED_LOGGED: bool = False
"""Module-level guard: deprecation INFO is emitted exactly once.

Reset by :func:`stop` so a stop→start cycle re-emits one INFO line
(useful for dev reload / test fixtures). Repeated ``start()`` calls
within the same process under ``AUTH_ENABLED=true`` are silent
no-ops after the first.
"""


def start() -> None:
    global _AUTH_DISABLED_LOGGED

    if auth_enabled():
        if not _AUTH_DISABLED_LOGGED:
            _log.info(
                "calendar_monitor: AUTH_ENABLED=true → legacy CALENDAR_* env "
                "path is disabled; configure CalDAV per user via "
                "PUT /api/v1/me/connector-credentials/caldav and add a "
                "`sources` row with source_type='caldav' "
                "(see docs/connect-and-verify.md)."
            )
            _AUTH_DISABLED_LOGGED = True
        return

    caldav_url = os.environ.get("CALENDAR_CALDAV_URL", "")
    if not caldav_url:
        _log.info("calendar_monitor: CALENDAR_CALDAV_URL not set — inactive")
        return

    poll_interval = int(os.environ.get("CALENDAR_POLL_INTERVAL", "1800"))

    scheduler = config.get_scheduler()
    if not scheduler.running:
        return

    scheduler.add_job(
        _poll_calendar,
        trigger="interval",
        seconds=poll_interval,
        id=_job_id,
        name="CalDAV calendar monitor",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _log.info("calendar_monitor: scheduled every %ds", poll_interval)


def stop() -> None:
    global _AUTH_DISABLED_LOGGED
    _AUTH_DISABLED_LOGGED = False
    try:
        scheduler = config.get_scheduler()
        job = scheduler.get_job(_job_id)
        if job:
            job.remove()
    except Exception as exc:
        _log.debug("calendar_monitor stop: %s", exc)


def _poll_calendar() -> None:
    caldav_url = os.environ.get("CALENDAR_CALDAV_URL", "")
    poll_interval = int(os.environ.get("CALENDAR_POLL_INTERVAL", "1800"))

    source = SourceConfig(
        id="__caldav__",
        name="CalDAV Calendar",
        source_type="caldav",
        url=caldav_url,
        category="calendar",
        active=True,
        poll_interval=poll_interval,
        extraction_method="caldav",
        css_selector_override=None,
        last_polled_at=None,
        last_signal_at=None,
        user_id=_LEGACY_USER_ID,
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
            process_signal(raw, user_id=_LEGACY_USER_ID)
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
