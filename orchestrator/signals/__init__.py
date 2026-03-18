"""Signal monitoring subsystem.

Coordinates feed_monitor, page_monitor, calendar_monitor, and system_monitor.
Call start_all() from main.py lifespan and stop_all() on shutdown.
"""

import logging

_log = logging.getLogger(__name__)


def start_all() -> None:
    """Start all signal monitors. Called from main.py lifespan."""
    from signals.feed_monitor import start as start_feeds
    from signals.page_monitor import start as start_pages
    from signals.calendar_monitor import start as start_calendar
    from signals.system_monitor import start as start_system

    start_feeds()
    start_pages()
    start_calendar()
    start_system()
    _log.info("Signal monitors started")


def stop_all() -> None:
    """Stop all signal monitors. Called from main.py shutdown."""
    from signals.feed_monitor import stop as stop_feeds
    from signals.page_monitor import stop as stop_pages
    from signals.calendar_monitor import stop as stop_calendar
    from signals.system_monitor import stop as stop_system

    stop_feeds()
    stop_pages()
    stop_calendar()
    stop_system()
    _log.info("Signal monitors stopped")
