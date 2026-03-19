# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""CalDAV calendar adapter (optional).

Active only when CALENDAR_CALDAV_URL is set. Uses the `caldav` library
(not in base requirements — import is guarded and failures are logged, not raised).

Returns Signal objects for upcoming events within a configurable window
(CALENDAR_LOOKAHEAD_HOURS, default 24h).
"""

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from models.signals import Signal, SourceConfig

_log = logging.getLogger(__name__)


class CalendarAdapter:
    def __init__(self, config: SourceConfig) -> None:
        self._config = config
        self._caldav_url = os.environ.get("CALENDAR_CALDAV_URL", config.url)
        self._username = os.environ.get("CALENDAR_USERNAME", "")
        self._password = os.environ.get("CALENDAR_PASSWORD", "")
        self._lookahead_hours = int(os.environ.get("CALENDAR_LOOKAHEAD_HOURS", "24"))

    # ------------------------------------------------------------------
    # Public interface (SignalSource protocol)
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        if not self._caldav_url:
            return False
        try:
            import caldav

            client = caldav.DAVClient(
                url=self._caldav_url,
                username=self._username,
                password=self._password,
            )
            client.principal()
            return True
        except ImportError:
            _log.warning("caldav library not installed — calendar adapter inactive")
            return False
        except Exception as exc:
            _log.debug("CalDAV ping failed: %s", exc)
            return False

    def poll(self) -> list[Signal]:
        """Return Signal objects for events starting within the lookahead window."""
        if not self._caldav_url:
            return []

        try:
            import caldav
        except ImportError:
            _log.warning("caldav library not installed — skipping calendar poll")
            return []

        now = datetime.now(timezone.utc)
        end = now + timedelta(hours=self._lookahead_hours)

        try:
            client = caldav.DAVClient(
                url=self._caldav_url,
                username=self._username,
                password=self._password,
            )
            principal = client.principal()
            calendars = principal.calendars()
        except Exception as exc:
            _log.error("CalDAV connection error: %s", exc)
            return []

        signals: list[Signal] = []
        for calendar in calendars:
            try:
                events = calendar.date_search(start=now, end=end, expand=True)
                for event in events:
                    sig = self._event_to_signal(event, now)
                    if sig:
                        signals.append(sig)
            except Exception as exc:
                _log.error("CalDAV event fetch error: %s", exc)

        _log.info("CalendarAdapter: %d upcoming events in next %dh", len(signals), self._lookahead_hours)
        return signals

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _event_to_signal(self, event, now: datetime) -> Optional[Signal]:
        try:
            vevent = event.vobject_instance.vevent
            summary = str(getattr(vevent, "summary", None) or "Calendar Event")
            dtstart = getattr(vevent, "dtstart", None)
            dtend = getattr(vevent, "dtend", None)
            description = str(getattr(vevent, "description", None) or "")
            location = str(getattr(vevent, "location", None) or "")
            uid = str(getattr(vevent, "uid", None) or uuid.uuid4())

            start_dt: datetime | None = None
            if dtstart:
                val = dtstart.value
                if hasattr(val, "tzinfo"):
                    start_dt = val if val.tzinfo else val.replace(tzinfo=timezone.utc)
                else:
                    # date-only event — convert to midnight UTC
                    start_dt = datetime(val.year, val.month, val.day, tzinfo=timezone.utc)

            raw = f"Event: {summary}"
            if location:
                raw += f"\nLocation: {location}"
            if description:
                raw += f"\nDescription: {description}"
            if start_dt:
                raw += f"\nStarts: {start_dt.isoformat()}"

            return Signal(
                signal_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"caldav::{uid}")),
                source_id=self._config.id,
                title=f"Upcoming: {summary}",
                url="",
                published_at=start_dt,
                content_summary="",
                raw_content=raw,
                entities=[],
                topics=["calendar", "event"],
                importance_score=0.0,
                relevance_score=0.0,
                notified=False,
                created_at=datetime.now(timezone.utc),
                user_id=self._config.user_id,
            )
        except Exception as exc:
            _log.debug("Skipping malformed CalDAV event: %s", exc)
            return None
