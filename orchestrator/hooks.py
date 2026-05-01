# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Hook dispatch for plugin extensibility.

- register(event, callback): attach a listener
- fire(event, *args, **kwargs): call all listeners synchronously
- fire_background(event, *args, **kwargs): call all listeners in a thread pool
- shutdown(): shut down the background executor
"""

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

_log = logging.getLogger(__name__)

_listeners: dict[str, list] = defaultdict(list)
_executor = ThreadPoolExecutor(max_workers=4)


def register(event: str, callback) -> None:
    _listeners[event].append(callback)
    _log.debug("Hook registered: %s -> %s", event, callback.__name__)


def fire(event: str, *args, **kwargs) -> None:
    for cb in _listeners.get(event, []):
        try:
            cb(*args, **kwargs)
        except Exception:
            _log.exception("Hook error in %s callback %s", event, cb.__name__)


def fire_background(event: str, *args, **kwargs) -> None:
    for cb in _listeners.get(event, []):
        _executor.submit(_safe_call, event, cb, *args, **kwargs)


def _safe_call(event: str, cb, *args, **kwargs) -> None:
    try:
        cb(*args, **kwargs)
    except Exception:
        _log.exception("Background hook error in %s callback %s", event, cb.__name__)


def shutdown() -> None:
    global _executor
    _executor.shutdown(wait=True)
    _executor = ThreadPoolExecutor(max_workers=4)
    _listeners.clear()
    _log.info("Hooks shutdown: executor stopped, listeners cleared")
