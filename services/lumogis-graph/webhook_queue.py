# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Internal worker pool for KG service `/webhook` projection work.

The KG service returns 202 Accepted as soon as a webhook envelope validates;
the actual `graph.writer.on_*` projection runs on a background thread so the
caller (Core) is never blocked by FalkorDB latency. This module is the
KG-side equivalent of Core's `hooks.fire_background` — same semantics, but
with a deliberately reduced public surface (`submit/qsize/shutdown` only).

Why this exists as its own module rather than inlined into routes/webhook.py:

  - `routes/health.py` needs `qsize()` for the `pending_webhook_tasks` field.
  - `main.py` lifespan needs `shutdown()` to drain the pool on SIGTERM
    instead of dropping in-flight projections.
  - The parity test harness (`tests/integration/wait_for_idle.py`) polls
    `pending_webhook_tasks` to know when KG has caught up after each
    `webhook_queue.submit` call.

Implementation notes:

  - Backed by a 4-worker `ThreadPoolExecutor`. The number is intentionally
    small: FalkorDB is single-writer-friendly and projections are
    I/O-bound on Postgres + FalkorDB, not CPU-bound, so going wider buys
    nothing and increases lock-contention risk.
  - `qsize()` is a `_inflight` counter incremented on `submit` and
    decremented in a `Future.add_done_callback`, NOT
    `executor._work_queue.qsize()`. The latter is private CPython API
    and has changed behaviour between minor versions; the wrapper gauge
    is stable across Python versions and includes both queued and
    actively-running tasks (which is what callers actually want — total
    work pending KG-side).
  - Logger name is `lumogis_graph.webhook_queue` so operators can
    `grep` and disambiguate KG worker logs from Core's `fire_background`
    pool logs in a shared journal.
"""

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

_log = logging.getLogger("lumogis_graph.webhook_queue")

# Tuneable via env at startup (main.py lifespan reads it). Default 4 matches
# the plan §"webhook_queue.py".
_DEFAULT_WORKERS = 4

_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()
_inflight = 0
_inflight_lock = threading.Lock()
_warned_high_water = False
_HIGH_WATER_THRESHOLD = 1000


def _get_executor() -> ThreadPoolExecutor:
    """Lazily create the executor on first use.

    `main.py` calls `init(workers=...)` from lifespan startup so the
    worker count can come from env, but tests that import this module
    directly should still get a working pool without explicit init.
    """
    global _executor
    if _executor is not None:
        return _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=_DEFAULT_WORKERS,
                thread_name_prefix="lumogis-graph-webhook",
            )
            _log.info("webhook_queue: ThreadPoolExecutor created (workers=%d)", _DEFAULT_WORKERS)
    return _executor


def init(workers: int = _DEFAULT_WORKERS) -> None:
    """(Re-)initialise the executor with `workers` threads.

    Called from `main.py` lifespan. Safe to call once per process; calling
    again after `shutdown()` rebuilds the pool.
    """
    global _executor, _inflight, _warned_high_water
    with _lock:
        if _executor is not None:
            _log.warning("webhook_queue: init() called with executor already initialised — replacing")
            try:
                _executor.shutdown(wait=False)
            except Exception:
                _log.exception("webhook_queue: replacing executor failed to shut down old one cleanly")
        _executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="lumogis-graph-webhook",
        )
    with _inflight_lock:
        _inflight = 0
        _warned_high_water = False
    _log.info("webhook_queue: initialised (workers=%d)", workers)


def _on_done(_fut: Future) -> None:
    """`add_done_callback` target: decrement in-flight gauge."""
    global _inflight
    with _inflight_lock:
        _inflight -= 1


def submit(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future:
    """Enqueue `fn(*args, **kwargs)` for background execution.

    Returns the `Future` so callers MAY observe completion in tests, but
    production callers (`routes/webhook.py`) discard it: the queue is
    the only synchronisation point and the caller has already returned
    202 to Core by the time `submit` returns.
    """
    global _inflight, _warned_high_water
    executor = _get_executor()
    fut = executor.submit(fn, *args, **kwargs)
    with _inflight_lock:
        _inflight += 1
        depth = _inflight
        should_warn = depth > _HIGH_WATER_THRESHOLD and not _warned_high_water
        if should_warn:
            _warned_high_water = True
    if should_warn:
        _log.warning(
            "webhook_queue: depth crossed %d (in_flight=%d) — projections may be falling behind",
            _HIGH_WATER_THRESHOLD,
            depth,
        )
    fut.add_done_callback(_on_done)
    return fut


def qsize() -> int:
    """Return the in-flight gauge (queued + actively-running tasks).

    This is the canonical contract for `GET /health.pending_webhook_tasks`
    and `tests/integration/wait_for_idle.py` — see the plan §"routes/health.py"
    and §"webhook_queue.py".
    """
    with _inflight_lock:
        return _inflight


def shutdown(wait: bool = True) -> None:
    """Drain (or abandon) in-flight tasks. Called from `main.py` lifespan teardown.

    With `wait=True` (the default), `ThreadPoolExecutor.shutdown(wait=True)`
    blocks until all queued projections finish. The KG service is sized
    for fast projections (single-digit ms each), so the lifespan
    teardown in practice waits well under one second; this is the price
    we pay for not losing webhooks already accepted with 202.
    """
    global _executor
    with _lock:
        if _executor is None:
            return
        ex = _executor
        _executor = None
    try:
        ex.shutdown(wait=wait)
        _log.info("webhook_queue: shutdown complete (wait=%s)", wait)
    except Exception:
        _log.exception("webhook_queue: shutdown failed")
