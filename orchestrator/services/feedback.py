"""Feedback recording service.

record_explicit(item_type, item_id, positive): thumbs up/down from user.
record_implicit(item_type, item_id, event_type): behavioural signal
  (e.g. item_opened, signal_dismissed, briefing_item_expanded).

Both fire Event.FEEDBACK_RECEIVED via hooks.fire_background() after writing.
"""

import logging
from typing import Optional

import config
import hooks
from events import Event

_log = logging.getLogger(__name__)


def record_explicit(
    item_type: str,
    item_id: str,
    positive: bool,
    user_id: str = "default",
) -> None:
    """Record an explicit thumbs-up/down feedback signal."""
    _write(item_type=item_type, item_id=item_id, positive=positive, event_type=None, user_id=user_id)
    hooks.fire_background(
        Event.FEEDBACK_RECEIVED,
        item_type=item_type,
        item_id=item_id,
        positive=positive,
        event_type=None,
    )


def record_implicit(
    item_type: str,
    item_id: str,
    event_type: str,
    user_id: str = "default",
) -> None:
    """Record an implicit behavioural feedback signal.

    event_type examples: item_opened, signal_dismissed, briefing_item_expanded.
    """
    _write(item_type=item_type, item_id=item_id, positive=None, event_type=event_type, user_id=user_id)
    hooks.fire_background(
        Event.FEEDBACK_RECEIVED,
        item_type=item_type,
        item_id=item_id,
        positive=None,
        event_type=event_type,
    )


def _write(
    item_type: str,
    item_id: str,
    positive: Optional[bool],
    event_type: Optional[str],
    user_id: str,
) -> None:
    try:
        ms = config.get_metadata_store()
        ms.execute(
            "INSERT INTO feedback_log "
            "(user_id, item_type, item_id, positive, event_type) "
            "VALUES (%s, %s, %s, %s, %s)",
            (user_id, item_type, item_id, positive, event_type),
        )
        _log.debug(
            "Feedback recorded: %s/%s positive=%s event=%s",
            item_type,
            item_id,
            positive,
            event_type,
        )
    except Exception as exc:
        _log.error("Feedback write error: %s", exc)
