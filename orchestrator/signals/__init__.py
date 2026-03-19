# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""Signal monitoring subsystem.

Coordinates feed_monitor, page_monitor, calendar_monitor, system_monitor, and digest.
Call start_all() from main.py lifespan and stop_all() on shutdown.
"""

import logging

_log = logging.getLogger(__name__)


def start_all() -> None:
    """Start all signal monitors and the digest scheduler."""
    from signals.feed_monitor import start as start_feeds
    from signals.page_monitor import start as start_pages
    from signals.calendar_monitor import start as start_calendar
    from signals.system_monitor import start as start_system
    from signals.digest import start as start_digest

    start_feeds()
    start_pages()
    start_calendar()
    start_system()
    start_digest()
    _log.info("Signal monitors started")


def stop_all() -> None:
    """Stop all signal monitors and the digest scheduler."""
    from signals.feed_monitor import stop as stop_feeds
    from signals.page_monitor import stop as stop_pages
    from signals.calendar_monitor import stop as stop_calendar
    from signals.system_monitor import stop as stop_system
    from signals.digest import stop as stop_digest

    stop_feeds()
    stop_pages()
    stop_calendar()
    stop_system()
    stop_digest()
    _log.info("Signal monitors stopped")
