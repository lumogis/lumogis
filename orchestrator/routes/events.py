# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""Server-Sent Events endpoint — real-time updates to connected clients.

GET /events — SSE stream. Events pushed via hooks on every:
  SIGNAL_RECEIVED         → new signal summary
  ACTION_EXECUTED         → action result
  ROUTINE_ELEVATION_READY → approval prompt

Architecture:
  - One asyncio.Queue per connected client.
  - Hook callbacks run in ThreadPoolExecutor threads; they call
    loop.call_soon_threadsafe(queue.put_nowait, event_str) to bridge.
  - A 5-minute circular buffer enables reconnection via Last-Event-ID.
  - Keepalive comments (": ping") sent every 15s to prevent proxy timeouts.
  - Events are user-scoped: only events matching the connected user_id are sent.
"""

import asyncio
import json
import logging
import time
from collections import deque
from typing import AsyncGenerator

import hooks
from auth import get_user
from events import Event
from fastapi import APIRouter
from fastapi import Request
from fastapi.responses import StreamingResponse

_log = logging.getLogger(__name__)

router = APIRouter(tags=["events"])

# ---------------------------------------------------------------------------
# Global connection registry and event buffer
# ---------------------------------------------------------------------------

# List of (user_id, asyncio.Queue, asyncio.AbstractEventLoop) per connection.
_connections: list[tuple[str, asyncio.Queue, asyncio.AbstractEventLoop]] = []

# Circular event buffer for Last-Event-ID reconnection.
# Each entry: (event_id: str, user_id: str, event_str: str, timestamp: float)
_buffer: deque = deque(maxlen=500)
_BUFFER_TTL = 300  # 5 minutes

# Monotonic event counter for IDs.
_event_counter = 0


# ---------------------------------------------------------------------------
# Hook callbacks (wired in main.py)
# ---------------------------------------------------------------------------


def _make_sse_event(event_type: str, data: dict, user_id: str = "default") -> str:
    """Build an SSE message string."""
    global _event_counter
    _event_counter += 1
    event_id = str(_event_counter)
    payload = json.dumps(data, default=str)
    msg = f"id: {event_id}\nevent: {event_type}\ndata: {payload}\n\n"

    # Buffer for reconnection.
    _buffer.append((event_id, user_id, msg, time.monotonic()))
    return msg


def _push_to_connections(event_type: str, data: dict, user_id: str = "default") -> None:
    """Called from hook callbacks (background thread). Thread-safe push."""
    event_str = _make_sse_event(event_type, data, user_id)
    for conn_user, queue, loop in list(_connections):
        if conn_user != user_id and conn_user != "__all__":
            continue
        try:
            loop.call_soon_threadsafe(queue.put_nowait, event_str)
        except Exception:
            pass


def on_signal_received(**kwargs) -> None:
    signal = kwargs.get("signal")
    if signal is None:
        return
    _push_to_connections(
        "signal_received",
        {
            "signal_id": signal.signal_id,
            "title": signal.title,
            "url": signal.url,
            "importance_score": signal.importance_score,
            "relevance_score": signal.relevance_score,
        },
        user_id=getattr(signal, "user_id", "default"),
    )


def on_action_executed(**kwargs) -> None:
    _push_to_connections(
        "action_executed",
        {
            "action_name": kwargs.get("action_name"),
            "connector": kwargs.get("connector"),
            "success": kwargs.get("success"),
            "reverse_token": kwargs.get("reverse_token"),
            "audit_id": kwargs.get("audit_id"),
        },
        user_id=kwargs.get("user_id", "default"),
    )


def on_routine_elevation_ready(**kwargs) -> None:
    _push_to_connections(
        "routine_elevation_ready",
        {
            "connector": kwargs.get("connector"),
            "action_type": kwargs.get("action_type"),
            "approval_count": kwargs.get("approval_count"),
        },
        user_id="default",
    )


def register_hooks() -> None:
    """Register SSE push callbacks on all relevant events. Call from main.py."""
    hooks.register(Event.SIGNAL_RECEIVED, on_signal_received)
    hooks.register(Event.ACTION_EXECUTED, on_action_executed)
    hooks.register(Event.ROUTINE_ELEVATION_READY, on_routine_elevation_ready)
    _log.info("SSE hooks registered")


# ---------------------------------------------------------------------------
# SSE endpoint
# ---------------------------------------------------------------------------


@router.get("/events")
async def sse_stream(request: Request):
    """Stream Server-Sent Events to the client.

    Supports Last-Event-ID header for reconnection: replays buffered events
    newer than the provided ID (up to 5 minutes old).
    """
    user = get_user(request)
    user_id = user.user_id if user else "default"

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    conn = (user_id, queue, loop)
    _connections.append(conn)

    # Replay missed events from buffer if Last-Event-ID is provided.
    last_id = request.headers.get("last-event-id") or request.headers.get("Last-Event-ID")
    if last_id:
        cutoff = time.monotonic() - _BUFFER_TTL
        replay = [
            (eid, uid, msg, ts)
            for eid, uid, msg, ts in _buffer
            if ts >= cutoff and (uid == user_id or uid == "__all__")
        ]
        # Replay events with ID greater than last_id.
        try:
            last_int = int(last_id)
            replay = [(eid, uid, msg, ts) for eid, uid, msg, ts in replay if int(eid) > last_int]
        except ValueError:
            replay = []

        for _, _, msg, _ in replay:
            await queue.put(msg)

    async def generate() -> AsyncGenerator[str, None]:
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield event
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            try:
                _connections.remove(conn)
            except ValueError:
                pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
