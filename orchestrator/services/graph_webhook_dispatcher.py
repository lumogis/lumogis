# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Core-side HTTP dispatcher for the out-of-process lumogis-graph service.

This module is the Core-side counterpart of `services/lumogis-graph/routes/`.
When `GRAPH_MODE == "service"`, Core's lifespan calls
`register_core_callbacks()` (defined here), which wires one outbound HTTP
webhook callback per knowledge-graph hook event onto the existing in-process
hook bus. From the rest of Core's perspective nothing changes — the hooks
fire exactly as they do today; the registered callback just happens to
serialise the payload into a `WebhookEnvelope` and POST it to the KG service
instead of writing to FalkorDB locally.

Why the indirection (callbacks rather than calling httpx directly from
ingest/extractor code):
  * Lets `GRAPH_MODE=inprocess` keep using the in-process plugin and
    `GRAPH_MODE=service` swap to HTTP transparently — no changes to
    ingest, extractor, or session code.
  * The hook bus already runs callbacks under `fire_background`'s
    ThreadPoolExecutor, so post-webhook latency does not block the
    request that triggered the event.
  * Keeps every retryable boundary (failed POST, KG-side 5xx, network
    blip) in one file with a uniform "log WARNING + return" contract,
    so a flaky KG service can't crash Core's ingest path.

Public surface (used by `main.py` lifespan and unit tests):
  * `post_webhook(event, payload)` — fire-and-forget POST to /webhook.
  * `get_context_sync(query, user_id, max_fragments)` — synchronous
    POST to /context with a hard 40 ms wall-clock timeout, used by the
    chat hot path (`routes/chat.py`).
  * `make_callback(event, payload_cls)` — factory; returns a kwargs-only
    function with the same signature as the in-process
    `plugins/graph/writer.py:on_<event>` so `hooks.register(...)` is
    drop-in.
  * `register_core_callbacks()` — wires all six callbacks onto the hook
    bus. Idempotent (subsequent calls are a no-op + WARNING log).
  * `_CALLBACKS_BY_EVENT` — populated by `register_core_callbacks()` so
    tests can introspect what got wired without touching `hooks._listeners`.
  * `shutdown()` — closes the lazy httpx.Client. Called from main.py
    lifespan teardown to avoid `ResourceWarning` on container stop.

Logging discipline:
  * Failures (non-200, network errors, JSON parse errors) log at WARNING
    with the response body (truncated). The chat path swallows the empty
    list silently per `routes/chat.py` rules — the WARNING is the only
    operator-visible signal.
  * `/context` timeouts are rate-limited to one INFO log per 60 s via a
    module-level monotonic counter so a slow KG service doesn't spam
    the journal at chat-message frequency.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from datetime import timezone
from typing import Callable

import hooks
import httpx
from events import Event
from models.webhook import AudioTranscribedPayload
from models.webhook import DocumentIngestedPayload
from models.webhook import EntityCreatedPayload
from models.webhook import EntityMergedPayload
from models.webhook import NoteCapturedPayload
from models.webhook import SessionEndedPayload
from models.webhook import WebhookEnvelope
from models.webhook import WebhookEvent
from pydantic import BaseModel
from pydantic import ValidationError

import config

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy httpx.Client
# ---------------------------------------------------------------------------

# Two distinct timeouts:
#   _WEBHOOK_TIMEOUT_S — generous (KG returns 202 fast; if it doesn't,
#                       the network is the problem, not KG processing time).
#   _CONTEXT_TIMEOUT_S — 40 ms, hard cap on the chat hot path. Includes
#                       connect+request+response. Anything over this and
#                       the chat reply continues without graph context.
_WEBHOOK_TIMEOUT_S = 5.0
_CONTEXT_TIMEOUT_S = 0.040

_client: httpx.Client | None = None
_client_lock = threading.Lock()


def _get_client() -> httpx.Client:
    """Return the module-level httpx.Client, creating it on first use.

    Reused across threads (httpx.Client is thread-safe). Pool size is the
    httpx default (10) which is plenty for the four-worker hook executor;
    every webhook completes in single-digit ms server-side.
    """
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(
                timeout=httpx.Timeout(_WEBHOOK_TIMEOUT_S),
                headers={"User-Agent": f"lumogis-core/{_user_agent_version()}"},
            )
            _log.debug("graph_webhook_dispatcher: httpx.Client created")
    return _client


def _user_agent_version() -> str:
    """Best-effort core version string for outbound User-Agent."""
    try:
        from __version__ import __version__  # type: ignore[import-not-found]

        return str(__version__)
    except Exception:
        return "unknown"


def shutdown() -> None:
    """Close the httpx.Client. Safe to call multiple times."""
    global _client
    with _client_lock:
        if _client is None:
            return
        try:
            _client.close()
        except Exception:
            _log.warning("graph_webhook_dispatcher: client.close() failed", exc_info=True)
        _client = None
        _log.info("graph_webhook_dispatcher: httpx.Client closed")


# ---------------------------------------------------------------------------
# Rate-limited timeout logger
# ---------------------------------------------------------------------------

_TIMEOUT_LOG_RATE_S = 60.0
_last_timeout_log_t = 0.0
_timeout_log_lock = threading.Lock()


def _log_context_timeout_rate_limited(elapsed_ms: int) -> None:
    """INFO-log a /context timeout at most once per 60 s.

    A flaky KG service would otherwise produce one log line per chat
    message. Operators care that timeouts are happening, not the rate;
    one ping per minute is enough signal.
    """
    global _last_timeout_log_t
    with _timeout_log_lock:
        now = time.monotonic()
        if now - _last_timeout_log_t < _TIMEOUT_LOG_RATE_S:
            return
        _last_timeout_log_t = now
    _log.info(
        "graph_webhook_dispatcher: /context timeout after %d ms (budget %d ms) — proceeding without graph context",
        elapsed_ms,
        int(_CONTEXT_TIMEOUT_S * 1000),
    )


# ---------------------------------------------------------------------------
# Webhook POST
# ---------------------------------------------------------------------------


def post_webhook(event: WebhookEvent, payload: BaseModel) -> None:
    """POST a `WebhookEnvelope(event=..., payload=...)` to KG `/webhook`.

    Never raises. Failures (network error, non-2xx, KG offline) log a
    WARNING and return. Callers MUST treat this as fire-and-forget;
    this is invoked from `hooks.fire_background`'s thread pool after
    the request that produced the event has already returned to the user.
    """
    base = config.get_kg_service_url()
    url = f"{base}/webhook"
    envelope = WebhookEnvelope(
        schema_version=1,
        event=event,
        occurred_at=datetime.now(timezone.utc),
        payload=payload.model_dump(mode="json"),
    )
    body = envelope.model_dump(mode="json")
    headers: dict[str, str] = {}
    secret = config.get_kg_webhook_secret()
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    try:
        client = _get_client()
        resp = client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        _log.warning(
            "graph_webhook_dispatcher: POST %s failed (%s) — event %s dropped, will reconcile",
            url,
            type(exc).__name__,
            event.value,
        )
        return
    except Exception:
        _log.exception("graph_webhook_dispatcher: unexpected error POSTing %s", url)
        return

    if resp.status_code >= 300:
        _log.warning(
            "graph_webhook_dispatcher: KG %s returned %d for event %s — body=%r",
            url,
            resp.status_code,
            event.value,
            _truncate_body(resp.text),
        )


def _truncate_body(text: str, max_len: int = 500) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "...(truncated)"


# ---------------------------------------------------------------------------
# Context POST (synchronous chat path)
# ---------------------------------------------------------------------------


def get_context_sync(
    query: str,
    user_id: str = "default",
    max_fragments: int = 3,
) -> list[str]:
    """Synchronous `/context` round-trip with a hard 40 ms wall-clock budget.

    Called from `routes/chat.py` once per chat turn when
    `GRAPH_MODE=service`. Returns the `fragments` list from the KG
    response on success, or `[]` on:
      - 40 ms wall-clock timeout (logged at INFO, rate-limited to 1/minute)
      - any HTTPError (logged at WARNING)
      - non-200 response (logged at WARNING)
      - malformed JSON / missing `fragments` field (logged at WARNING)

    The empty-list-on-failure contract is what lets the chat path stay
    a single-line addition: `fragments.extend(get_context_sync(...))`.
    """
    base = config.get_kg_service_url()
    url = f"{base}/context"
    body = {"query": query, "user_id": user_id, "max_fragments": max_fragments}
    headers: dict[str, str] = {}
    secret = config.get_kg_webhook_secret()
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    t0 = time.monotonic()
    try:
        client = _get_client()
        resp = client.post(url, json=body, headers=headers, timeout=_CONTEXT_TIMEOUT_S)
    except httpx.TimeoutException:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        _log_context_timeout_rate_limited(elapsed_ms)
        return []
    except httpx.HTTPError as exc:
        _log.warning(
            "graph_webhook_dispatcher: GET context failed (%s) — proceeding without graph context",
            type(exc).__name__,
        )
        return []
    except Exception:
        _log.exception("graph_webhook_dispatcher: unexpected error on /context")
        return []

    if resp.status_code != 200:
        _log.warning(
            "graph_webhook_dispatcher: KG /context returned %d — body=%r",
            resp.status_code,
            _truncate_body(resp.text),
        )
        return []

    try:
        data = resp.json()
    except Exception:
        _log.warning("graph_webhook_dispatcher: KG /context returned non-JSON body")
        return []

    fragments = data.get("fragments")
    if not isinstance(fragments, list):
        _log.warning(
            "graph_webhook_dispatcher: KG /context response missing 'fragments' list — got %r",
            data,
        )
        return []
    return [str(f) for f in fragments]


# ---------------------------------------------------------------------------
# Callback factory + registration
# ---------------------------------------------------------------------------


def make_callback(event: WebhookEvent, payload_cls: type[BaseModel]) -> Callable[..., None]:
    """Return a kwargs-only callback that builds `payload_cls(**kwargs)` and posts it.

    The returned function MUST be drop-in compatible with the in-process
    `plugins/graph/writer.py:on_<event>` for the matching event so that
    Core's ingest/extractor/session code sees no behavioural difference
    between `GRAPH_MODE=inprocess` and `GRAPH_MODE=service`. The
    in-process handlers all use kwargs-only signatures with `**_kw`
    sinks (see `writer.py:291-407`); we mirror that here so an unknown
    extra kwarg from a future hook contract does not crash the
    serialiser.
    """

    def _callback(**kwargs) -> None:
        try:
            payload = payload_cls(
                **{k: v for k, v in kwargs.items() if k in payload_cls.model_fields}
            )
        except ValidationError as exc:
            _log.warning(
                "graph_webhook_dispatcher: %s payload validation failed — event dropped (%s)",
                event.value,
                exc,
            )
            return
        post_webhook(event, payload)

    _callback.__name__ = f"webhook_dispatch_{event.value}"
    _callback.__qualname__ = _callback.__name__
    return _callback


# Maps (Core hook bus Event constant, KG WebhookEvent enum, Pydantic payload class).
# Single source of truth for what Core dispatches when GRAPH_MODE=service.
# Adding a new hook event requires three coordinated changes (Core hook
# Event constant, KG WebhookEvent member, payload class in models/webhook.py)
# AND an entry here. The contract test
# `test_register_core_callbacks_covers_every_event` enforces it.
_EVENT_REGISTRATION: list[tuple[str, WebhookEvent, type[BaseModel]]] = [
    (Event.DOCUMENT_INGESTED, WebhookEvent.DOCUMENT_INGESTED, DocumentIngestedPayload),
    (Event.ENTITY_CREATED, WebhookEvent.ENTITY_CREATED, EntityCreatedPayload),
    (Event.SESSION_ENDED, WebhookEvent.SESSION_ENDED, SessionEndedPayload),
    (Event.ENTITY_MERGED, WebhookEvent.ENTITY_MERGED, EntityMergedPayload),
    (Event.NOTE_CAPTURED, WebhookEvent.NOTE_CAPTURED, NoteCapturedPayload),
    (Event.AUDIO_TRANSCRIBED, WebhookEvent.AUDIO_TRANSCRIBED, AudioTranscribedPayload),
]

# Populated by `register_core_callbacks()`. Useful for tests; also lets the
# operator query the live registration by `import services.graph_webhook_dispatcher`.
_CALLBACKS_BY_EVENT: dict[WebhookEvent, Callable[..., None]] = {}
_registered = False
_register_lock = threading.Lock()


def register_core_callbacks() -> None:
    """Wire all six webhook callbacks onto the in-process hook bus.

    Called from `orchestrator/main.py` lifespan in `GRAPH_MODE=service`
    branch. Idempotent: a second call logs a WARNING and returns
    (re-registering would double-fire every event).
    """
    global _registered
    with _register_lock:
        if _registered:
            _log.warning(
                "graph_webhook_dispatcher: register_core_callbacks() called twice — ignoring"
            )
            return
        for hook_name, wevent, payload_cls in _EVENT_REGISTRATION:
            cb = make_callback(wevent, payload_cls)
            hooks.register(hook_name, cb)
            _CALLBACKS_BY_EVENT[wevent] = cb
        _registered = True
        _log.info(
            "graph_webhook_dispatcher: registered %d outbound webhook callbacks (KG service mode)",
            len(_EVENT_REGISTRATION),
        )


def _reset_for_tests() -> None:
    """Reset module-level state. ONLY for use by unit tests' autouse fixtures."""
    global _registered, _last_timeout_log_t
    with _register_lock:
        _registered = False
        _CALLBACKS_BY_EVENT.clear()
    _last_timeout_log_t = 0.0
