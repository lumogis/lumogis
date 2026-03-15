"""Ask/Do permission enforcement.

Every tool call passes through check_permission() before execution.
Connectors default to ASK mode (read-only). DO mode is explicitly
enabled per connector via PUT /permissions/{connector}.
"""

import logging
from datetime import datetime
from datetime import timezone

import config

_log = logging.getLogger(__name__)

_mode_cache: dict[str, str] = {}

_DEFAULT_MODE = "ASK"
_VALID_MODES = {"ASK", "DO"}


def get_connector_mode(connector: str) -> str:
    if connector in _mode_cache:
        return _mode_cache[connector]
    store = config.get_metadata_store()
    row = store.fetch_one(
        "SELECT mode FROM connector_permissions WHERE connector = %s",
        (connector,),
    )
    mode = row["mode"] if row else _DEFAULT_MODE
    _mode_cache[connector] = mode
    return mode


def invalidate_cache(connector: str) -> None:
    _mode_cache.pop(connector, None)


def check_permission(connector: str, action_type: str, is_write: bool) -> bool:
    mode = get_connector_mode(connector)
    allowed = True
    if is_write and mode == "ASK":
        allowed = False
    log_action(
        connector=connector,
        action_type=action_type,
        mode=mode,
        allowed=allowed,
    )
    return allowed


def log_action(
    connector: str,
    action_type: str,
    mode: str,
    allowed: bool,
    input_summary: str | None = None,
    result_summary: str | None = None,
    reverse_action: str | None = None,
) -> None:
    store = config.get_metadata_store()
    try:
        store.execute(
            """INSERT INTO action_log
               (connector, action_type, mode, allowed, input_summary,
                result_summary, reverse_action, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                connector,
                action_type,
                mode,
                allowed,
                input_summary,
                result_summary,
                reverse_action,
                datetime.now(timezone.utc),
            ),
        )
    except Exception:
        _log.exception("Failed to log action for %s/%s", connector, action_type)


def set_connector_mode(connector: str, mode: str) -> None:
    if mode not in _VALID_MODES:
        raise ValueError(f"Invalid mode: {mode}. Must be one of {_VALID_MODES}")
    store = config.get_metadata_store()
    store.execute(
        """INSERT INTO connector_permissions (connector, mode)
           VALUES (%s, %s)
           ON CONFLICT (connector) DO UPDATE SET mode = EXCLUDED.mode""",
        (connector, mode),
    )
    invalidate_cache(connector)
    _log.info("Permission changed: %s -> %s", connector, mode)


def seed_defaults() -> None:
    store = config.get_metadata_store()
    store.execute(
        """INSERT INTO connector_permissions (connector, mode)
           VALUES (%s, %s)
           ON CONFLICT (connector) DO NOTHING""",
        ("filesystem-mcp", "ASK"),
    )
    _log.info("Default permissions seeded")


def get_all_permissions() -> list[dict]:
    store = config.get_metadata_store()
    return store.fetch_all("SELECT connector, mode FROM connector_permissions ORDER BY connector")
