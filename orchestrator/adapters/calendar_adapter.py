# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""CalDAV calendar adapter (optional).

Active when a per-user CalDAV credential row exists (or, under
``AUTH_ENABLED=false``, when ``CALENDAR_CALDAV_URL`` is set in the
process environment as the ``"default"`` user's fallback). Uses the
``caldav`` library (not in base requirements — import is guarded and
failures are logged, not raised).

Returns Signal objects for upcoming events within a configurable
window (``CALENDAR_LOOKAHEAD_HOURS``, default 24h — deployment-wide
in v1; per-user lookahead is a deferred follow-up).

Credential resolution lives in :mod:`services.caldav_credentials`;
this adapter calls :func:`services.caldav_credentials.load_connection`
once per poll cycle and caches the result on the instance. ``ping``
and ``poll`` MUST NEVER raise out of this module — every resolution
failure path returns ``False`` / ``[]`` after a structured warning
log whose fields are exactly ``{user_id, connector, code}``. The
exception object is never logged on the skip path (security: the
caldav / requests / urllib3 stack can carry credential URLs in
``repr(exc)``); pinned by
``test_adapter_skip_log_does_not_leak_credentials``.
"""

import logging
import os
import uuid
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Optional

from connectors.registry import CALDAV
from connectors.registry import UnknownConnector
from models.signals import Signal
from models.signals import SourceConfig
from services.caldav_credentials import CaldavConnection
from services.connector_credentials import ConnectorNotConfigured
from services.connector_credentials import CredentialUnavailable
from services.point_ids import caldav_signal_id

from services import caldav_credentials

_log = logging.getLogger(__name__)


_CONNECTION_UNRESOLVED = object()
"""Sentinel for the per-instance connection cache.

Distinguishes "not yet resolved" from "resolved to None" (failed
resolution that should still be re-attempted on next poll cycle =
next ``CalendarAdapter`` instance, never on the same instance).
"""


class CalendarAdapter:
    def __init__(self, config: SourceConfig) -> None:
        self._config = config
        self._lookahead_hours = int(os.environ.get("CALENDAR_LOOKAHEAD_HOURS", "24"))
        self._connection: object = _CONNECTION_UNRESOLVED

    # ------------------------------------------------------------------
    # Public interface (SignalSource protocol)
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        conn = self._get_connection()
        if conn is None:
            return False
        try:
            import caldav

            client = caldav.DAVClient(
                url=conn.base_url,
                username=conn.username,
                password=conn.password,
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
        conn = self._get_connection()
        if conn is None:
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
                url=conn.base_url,
                username=conn.username,
                password=conn.password,
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

        _log.info(
            "CalendarAdapter: %d upcoming events in next %dh", len(signals), self._lookahead_hours
        )
        return signals

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_connection(self) -> Optional[CaldavConnection]:
        """Resolve and cache the per-user CalDAV connection.

        Returns ``None`` (after a structured WARNING) when:

        * no row exists and the env fallback is unusable
          (``ConnectorNotConfigured``) — code ``connector_not_configured``;
        * the registry wiring regressed and ``caldav`` is unknown
          (``UnknownConnector`` — defensive; should be unreachable
          per ``test_caldav_is_registered``) — code
          ``connector_not_configured``;
        * the substrate decrypt failed or the payload is structurally
          malformed (``CredentialUnavailable``) — code
          ``credential_unavailable``;
        * a required field is empty or ``base_url`` fails the URL
          shape rule (``ValueError``) — code ``credential_unavailable``.

        Logging discipline (security-critical): the exception object
        is NEVER included in the log record. No ``%s`` / ``%r`` of
        the exception, no ``exc_info=`` — the caldav / requests /
        urllib3 stack can carry credential URLs in ``repr(exc)``.
        Pinned by ``test_adapter_skip_log_does_not_leak_credentials``.

        The cache lifetime is the ``CalendarAdapter`` instance (one
        instance per scheduled poll cycle), so credential rotation
        and row deletion take effect on the next poll without a
        process restart.
        """
        if self._connection is not _CONNECTION_UNRESOLVED:
            return self._connection  # type: ignore[return-value]

        try:
            conn = caldav_credentials.load_connection(self._config.user_id)
        except ConnectorNotConfigured:
            self._log_skip("connector_not_configured")
            self._connection = None
            return None
        except UnknownConnector:
            # Defensive — registry regressed and dropped 'caldav'.
            # Maps to connector_not_configured per plan §Error
            # handling contract (mis-deploy → no poll).
            self._log_skip("connector_not_configured")
            self._connection = None
            return None
        except CredentialUnavailable:
            self._log_skip("credential_unavailable")
            self._connection = None
            return None
        except ValueError:
            # Empty required field, or base_url failed the URL rule.
            # Maps to credential_unavailable (a row IS present, it's
            # just unusable). Never connector_not_configured.
            self._log_skip("credential_unavailable")
            self._connection = None
            return None

        self._connection = conn
        return conn

    def _log_skip(self, code: str) -> None:
        """Emit the structured "poll skipped" warning.

        Fields are exactly ``{user_id, connector, code}`` — the
        exception object is deliberately excluded (see
        :meth:`_get_connection` docstring). The message is a fixed
        literal, never a format string carrying credential
        substrings.
        """
        _log.warning(
            "caldav: poll skipped",
            extra={
                "user_id": self._config.user_id,
                "connector": CALDAV,
                "code": code,
            },
        )

    def _event_to_signal(self, event, now: datetime) -> Optional[Signal]:
        try:
            vevent = event.vobject_instance.vevent
            summary = str(getattr(vevent, "summary", None) or "Calendar Event")
            dtstart = getattr(vevent, "dtstart", None)
            _dtend = getattr(vevent, "dtend", None)
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
                signal_id=caldav_signal_id(self._config.user_id, uid),
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
