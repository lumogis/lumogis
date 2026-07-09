# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Lightweight, cached per-service health for the web client (LUM-512).

The admin stack-status snapshot is rich but admin-only. The web client needs a
*non-admin*, cheap signal to drive graceful-degradation banners (Ollama / graph
store / Qdrant down), polled by every chat client.

Two deliberate narrowings vs the admin snapshot:

* **Whitelist** — only the services the client actually consumes are exposed
  (:data:`_CLIENT_SERVICE_IDS`). A non-admin must not be able to enumerate the
  full internal topology (caddy, mongodb, librechat, stack_control, …) via this
  endpoint; that stays admin-only on ``/admin/diagnostics/stack-status``.
* **Lean probe** — :func:`stack_status.build_service_states` skips the storage
  ``statvfs`` scan and the Ollama model-list HTTP call the admin snapshot does,
  since this endpoint surfaces neither.

The poll is only a *proactive* signal — the response to the actual chat/search
request is the source of truth for live errors. The web client invalidates its
cached health on an error response so a freshly-down service surfaces at once.
"""

from __future__ import annotations

import threading
import time

from models.api_v1 import HealthResponse

from services import stack_status as stack_status_svc

# The only service ids the web client reads (see useServiceHealth.ts). Anything
# else in the stack-status snapshot is withheld from non-admin callers.
_CLIENT_SERVICE_IDS = frozenset({"ollama", "qdrant", "graph"})

# Short enough that recovery/outage shows up promptly; long enough that N chat
# clients polling ~every 20s collapse to roughly one probe per interval.
_TTL_SEC = 10.0

_lock = threading.Lock()
_cache: tuple[float, HealthResponse] | None = None
_rebuilding = False


def _build_uncached() -> HealthResponse:
    """Probe service states and project to the non-sensitive, whitelisted DTO."""
    overall, services = stack_status_svc.build_service_states()
    projected = {svc.id: svc.state for svc in services if svc.id in _CLIENT_SERVICE_IDS}
    return HealthResponse(overall=overall, services=projected)


def get_user_health(*, force: bool = False) -> HealthResponse:
    """Return the cached health snapshot, rebuilding if older than the TTL.

    Serve-stale-while-revalidate: the slow probe runs **outside** the lock, so a
    rebuild never blocks concurrent callers — if another caller is already
    revalidating and we hold a prior snapshot, we return the stale one rather
    than queueing behind the probe. ``force=True`` always rebuilds.
    """
    global _cache, _rebuilding
    with _lock:
        now = time.monotonic()
        if not force and _cache is not None and (now - _cache[0]) < _TTL_SEC:
            return _cache[1]
        if not force and _rebuilding and _cache is not None:
            return _cache[1]
        _rebuilding = True
    try:
        result = _build_uncached()
        with _lock:
            _cache = (time.monotonic(), result)
        return result
    finally:
        with _lock:
            _rebuilding = False


def reset_cache() -> None:
    """Drop the cached snapshot (test hook; also safe to call on shutdown)."""
    global _cache, _rebuilding
    with _lock:
        _cache = None
        _rebuilding = False
