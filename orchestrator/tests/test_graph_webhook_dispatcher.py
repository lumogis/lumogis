# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for orchestrator/services/graph_webhook_dispatcher.py.

The dispatcher is the Core-side counterpart to the lumogis-graph service's
`/webhook` and `/context` endpoints. These tests pin the wire contract,
the swallowing-vs-raising behaviour on errors, the bearer-token attachment,
and the 40 ms `/context` budget — all the things that, if regressed,
silently degrade graph quality without throwing a visible error.

Network is never actually opened: every test injects an `httpx.MockTransport`
that intercepts the request and lets the test inspect / shape the response.
"""

from __future__ import annotations

import inspect
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

import config
import hooks
from events import Event
from models.webhook import (
    EntityCreatedPayload,
    EntityMergedPayload,
    WebhookEnvelope,
    WebhookEvent,
)
from services import graph_webhook_dispatcher as dispatcher


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_dispatcher(monkeypatch):
    """Reset module state and provide a clean URL/secret for every test.

    We snapshot+restore the per-event listener lists we touch instead of
    calling `hooks.shutdown()`: the latter clears `_listeners` globally,
    including module-import-time registrations like
    `services/tools.py:_add_plugin_tool` which do not re-register between
    pytest items (modules import once per process).
    """
    dispatcher.shutdown()
    dispatcher._reset_for_tests()
    monkeypatch.setenv("KG_SERVICE_URL", "http://kg-test.local:8001")
    monkeypatch.delenv("GRAPH_WEBHOOK_SECRET", raising=False)
    touched_events = [
        Event.DOCUMENT_INGESTED, Event.ENTITY_CREATED, Event.SESSION_ENDED,
        Event.ENTITY_MERGED, Event.NOTE_CAPTURED, Event.AUDIO_TRANSCRIBED,
    ]
    snapshots = {e: list(hooks._listeners.get(e, [])) for e in touched_events}
    yield
    dispatcher.shutdown()
    dispatcher._reset_for_tests()
    for e, listeners in snapshots.items():
        hooks._listeners[e] = listeners


def _install_mock_client(handler) -> list[httpx.Request]:
    """Replace the dispatcher's lazy client with one that uses MockTransport.

    Returns the list the handler appends each captured request to, so tests
    can inspect what got sent without coupling to httpx internals.
    """
    captured: list[httpx.Request] = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    transport = httpx.MockTransport(_wrapped)
    test_client = httpx.Client(transport=transport, timeout=httpx.Timeout(5.0))
    dispatcher._client = test_client
    return captured


# ---------------------------------------------------------------------------
# make_callback signature parity with the in-process writer
# ---------------------------------------------------------------------------


def test_make_callback_signature_accepts_entity_created_kwargs():
    """The dispatcher callback must accept the same kwargs as
    `plugins/graph/writer.py:on_entity_created` so `hooks.register` is a
    drop-in replacement (Core's ingest code fires the event with kwargs
    only — `hooks.fire(Event.ENTITY_CREATED, entity_id=..., name=..., ...)`).
    """
    cb = dispatcher.make_callback(WebhookEvent.ENTITY_CREATED, EntityCreatedPayload)
    sig = inspect.signature(cb)
    # We use a kwargs-only callback so any current OR future hook field
    # is forwarded; verify the catch-all kwargs is present.
    assert any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    ), f"callback must accept **kwargs, got {sig}"


def test_make_callback_drops_unknown_kwargs_silently():
    """Forward-compat: a future hook payload with an extra field must not
    crash the dispatcher. The callback filters to the payload model's
    fields before constructing it.
    """
    captured = _install_mock_client(lambda req: httpx.Response(202))
    cb = dispatcher.make_callback(WebhookEvent.ENTITY_MERGED, EntityMergedPayload)
    cb(winner_id="W", loser_id="L", user_id="u", future_field="ignored")
    assert len(captured) == 1
    body = captured[0].read()
    envelope = WebhookEnvelope.model_validate_json(body)
    assert "future_field" not in envelope.payload


def test_make_callback_drops_event_on_validation_error(caplog):
    """Missing required fields should NOT raise (would crash the hook executor);
    instead, log a WARNING and drop."""
    captured = _install_mock_client(lambda req: httpx.Response(202))
    cb = dispatcher.make_callback(WebhookEvent.ENTITY_MERGED, EntityMergedPayload)
    with caplog.at_level(logging.WARNING, logger="services.graph_webhook_dispatcher"):
        cb(winner_id="W")  # missing loser_id, user_id
    assert captured == [], "no HTTP call should be made when payload validation fails"
    assert any("validation failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# post_webhook
# ---------------------------------------------------------------------------


def test_post_webhook_posts_well_formed_envelope_to_kg():
    captured = _install_mock_client(lambda req: httpx.Response(202))
    payload = EntityMergedPayload(winner_id="W", loser_id="L", user_id="u")

    dispatcher.post_webhook(WebhookEvent.ENTITY_MERGED, payload)

    assert len(captured) == 1
    req = captured[0]
    assert req.url == httpx.URL("http://kg-test.local:8001/webhook")
    assert req.method == "POST"
    envelope = WebhookEnvelope.model_validate_json(req.read())
    assert envelope.event == WebhookEvent.ENTITY_MERGED
    assert envelope.schema_version == 1
    assert envelope.payload == {"winner_id": "W", "loser_id": "L", "user_id": "u"}
    assert envelope.occurred_at.tzinfo is not None


def test_post_webhook_swallows_network_errors(caplog):
    def boom(_req):
        raise httpx.ConnectError("simulated outage")

    _install_mock_client(boom)
    payload = EntityMergedPayload(winner_id="W", loser_id="L", user_id="u")

    with caplog.at_level(logging.WARNING, logger="services.graph_webhook_dispatcher"):
        dispatcher.post_webhook(WebhookEvent.ENTITY_MERGED, payload)  # MUST NOT raise

    assert any("POST" in r.message and "failed" in r.message for r in caplog.records)


def test_post_webhook_logs_warning_on_kg_5xx(caplog):
    _install_mock_client(lambda _req: httpx.Response(500, text="upstream failure"))
    payload = EntityMergedPayload(winner_id="W", loser_id="L", user_id="u")

    with caplog.at_level(logging.WARNING, logger="services.graph_webhook_dispatcher"):
        dispatcher.post_webhook(WebhookEvent.ENTITY_MERGED, payload)

    assert any("returned 500" in r.message for r in caplog.records)


def test_post_webhook_attaches_bearer_when_secret_set(monkeypatch):
    monkeypatch.setenv("GRAPH_WEBHOOK_SECRET", "supersecret")
    captured = _install_mock_client(lambda _req: httpx.Response(202))
    payload = EntityMergedPayload(winner_id="W", loser_id="L", user_id="u")

    dispatcher.post_webhook(WebhookEvent.ENTITY_MERGED, payload)

    assert captured[0].headers["authorization"] == "Bearer supersecret"


def test_post_webhook_omits_authorization_when_secret_unset():
    captured = _install_mock_client(lambda _req: httpx.Response(202))
    payload = EntityMergedPayload(winner_id="W", loser_id="L", user_id="u")

    dispatcher.post_webhook(WebhookEvent.ENTITY_MERGED, payload)

    assert "authorization" not in captured[0].headers


# ---------------------------------------------------------------------------
# get_context_sync
# ---------------------------------------------------------------------------


def test_get_context_sync_returns_fragments_on_200():
    def handler(_req):
        return httpx.Response(200, json={"fragments": ["[Graph] Ada knew Babbage."]})

    captured = _install_mock_client(handler)
    fragments = dispatcher.get_context_sync(query="ada", user_id="u", max_fragments=3)

    assert fragments == ["[Graph] Ada knew Babbage."]
    body = captured[0].read()
    assert b'"query":"ada"' in body
    assert b'"max_fragments":3' in body


def test_get_context_sync_returns_empty_on_500(caplog):
    _install_mock_client(lambda _req: httpx.Response(500, text="boom"))
    with caplog.at_level(logging.WARNING, logger="services.graph_webhook_dispatcher"):
        fragments = dispatcher.get_context_sync(query="ada")
    assert fragments == []
    assert any("returned 500" in r.message for r in caplog.records)


def test_get_context_sync_returns_empty_on_network_error():
    def boom(_req):
        raise httpx.ConnectError("kg gone")
    _install_mock_client(boom)
    assert dispatcher.get_context_sync(query="ada") == []


def test_get_context_sync_returns_empty_on_malformed_json(caplog):
    _install_mock_client(lambda _req: httpx.Response(200, text="not-json"))
    with caplog.at_level(logging.WARNING, logger="services.graph_webhook_dispatcher"):
        fragments = dispatcher.get_context_sync(query="ada")
    assert fragments == []
    assert any("non-JSON" in r.message for r in caplog.records)


def test_get_context_sync_returns_empty_when_fragments_field_missing(caplog):
    _install_mock_client(lambda _req: httpx.Response(200, json={"oops": []}))
    with caplog.at_level(logging.WARNING, logger="services.graph_webhook_dispatcher"):
        fragments = dispatcher.get_context_sync(query="ada")
    assert fragments == []
    assert any("missing 'fragments'" in r.message for r in caplog.records)


def test_get_context_sync_attaches_bearer(monkeypatch):
    monkeypatch.setenv("GRAPH_WEBHOOK_SECRET", "ctx-secret")
    captured = _install_mock_client(
        lambda _req: httpx.Response(200, json={"fragments": []})
    )
    dispatcher.get_context_sync(query="ada")
    assert captured[0].headers["authorization"] == "Bearer ctx-secret"


class _SlowHandler(BaseHTTPRequestHandler):
    """HTTP handler that sleeps before replying, to force a real httpx timeout.

    httpx.MockTransport bypasses the connection-pool / I/O layer that enforces
    per-request timeouts, so the only honest way to test the 40 ms `/context`
    budget is against a real local socket. Stdlib `http.server` is enough.
    """
    sleep_seconds = 0.15

    def do_POST(self):  # noqa: N802 — stdlib spelling
        time.sleep(self.sleep_seconds)
        body = json.dumps({"fragments": ["never seen"]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # silence stdlib's stderr spam
        return


@pytest.fixture
def slow_kg_server(monkeypatch):
    """Run a localhost HTTP server that sleeps 150 ms before responding."""
    server = HTTPServer(("127.0.0.1", 0), _SlowHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("KG_SERVICE_URL", f"http://127.0.0.1:{port}")
    # Real client (no MockTransport) so the timeout is enforced by httpx.
    dispatcher._client = httpx.Client(timeout=httpx.Timeout(5.0))
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def test_get_context_sync_timeout_returns_empty_within_budget(slow_kg_server, caplog):
    """A KG that sleeps past the 40 ms budget must NOT block the chat path.

    The local server sleeps 150 ms while the dispatcher's per-request timeout
    is the production 40 ms; the dispatcher must abort with `[]` well before
    the server would have replied.
    """
    t0 = time.monotonic()
    with caplog.at_level(logging.INFO, logger="services.graph_webhook_dispatcher"):
        fragments = dispatcher.get_context_sync(query="ada")
    elapsed_ms = (time.monotonic() - t0) * 1000

    assert fragments == []
    assert elapsed_ms < 140, (
        f"chat path waited {elapsed_ms:.0f} ms — must abort at the 40 ms budget"
    )
    assert any("/context timeout" in r.message for r in caplog.records)


def test_get_context_sync_timeout_log_is_rate_limited(slow_kg_server, caplog):
    """A flaky KG would otherwise produce one INFO line per chat message."""
    with caplog.at_level(logging.INFO, logger="services.graph_webhook_dispatcher"):
        dispatcher.get_context_sync(query="ada")
        dispatcher.get_context_sync(query="ada")
        dispatcher.get_context_sync(query="ada")

    timeout_lines = [r for r in caplog.records if "/context timeout" in r.message]
    assert len(timeout_lines) == 1, (
        f"expected exactly one rate-limited INFO log, got {len(timeout_lines)}"
    )


# ---------------------------------------------------------------------------
# register_core_callbacks
# ---------------------------------------------------------------------------


def test_register_core_callbacks_wires_all_six_events():
    dispatcher.register_core_callbacks()
    expected = {
        WebhookEvent.DOCUMENT_INGESTED,
        WebhookEvent.ENTITY_CREATED,
        WebhookEvent.SESSION_ENDED,
        WebhookEvent.ENTITY_MERGED,
        WebhookEvent.NOTE_CAPTURED,
        WebhookEvent.AUDIO_TRANSCRIBED,
    }
    assert set(dispatcher._CALLBACKS_BY_EVENT.keys()) == expected


def test_register_core_callbacks_is_idempotent(caplog):
    """A second call must log a WARNING and NOT re-register any callbacks
    (would double-fire every webhook). We check by counting how many of the
    listeners on ENTITY_CREATED were created by the dispatcher (their
    `__name__` starts with `webhook_dispatch_`) — the in-process plugin's
    `on_entity_created` is also present in this test environment but is
    unrelated to the dispatcher.
    """
    dispatcher.register_core_callbacks()
    with caplog.at_level(logging.WARNING, logger="services.graph_webhook_dispatcher"):
        dispatcher.register_core_callbacks()
    assert any("called twice" in r.message for r in caplog.records)
    dispatcher_listeners = [
        cb
        for cb in hooks._listeners.get(Event.ENTITY_CREATED, [])
        if getattr(cb, "__name__", "").startswith("webhook_dispatch_")
    ]
    assert len(dispatcher_listeners) == 1, (
        f"expected exactly one webhook_dispatch_ listener, got {len(dispatcher_listeners)}"
    )


def test_register_core_callbacks_callbacks_fire_through_hook_bus():
    captured = _install_mock_client(lambda _req: httpx.Response(202))
    dispatcher.register_core_callbacks()

    hooks.fire(
        Event.ENTITY_MERGED,
        winner_id="W", loser_id="L", user_id="u",
    )

    assert len(captured) == 1
    envelope = WebhookEnvelope.model_validate_json(captured[0].read())
    assert envelope.event == WebhookEvent.ENTITY_MERGED


def test_register_core_callbacks_covers_every_webhook_event():
    """Contract test: every WebhookEvent member MUST be in _EVENT_REGISTRATION,
    otherwise that event will silently drop on the wire when GRAPH_MODE=service.
    """
    registered_events = {e for _hook, e, _cls in dispatcher._EVENT_REGISTRATION}
    assert registered_events == set(WebhookEvent), (
        f"missing wire-up for: {set(WebhookEvent) - registered_events}"
    )


# ---------------------------------------------------------------------------
# Module config sanity
# ---------------------------------------------------------------------------


def test_kg_service_url_from_env(monkeypatch):
    monkeypatch.setenv("KG_SERVICE_URL", "https://kg.example.com:9000/")
    assert config.get_kg_service_url() == "https://kg.example.com:9000"


def test_kg_webhook_secret_blank_treated_as_unset(monkeypatch):
    monkeypatch.setenv("GRAPH_WEBHOOK_SECRET", "   ")
    assert config.get_kg_webhook_secret() is None
