# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for the request-correlation middleware (chunk: structured_audit_logging).

These tests build a small standalone FastAPI app that mounts only
``correlation_middleware`` (and a minimal route that simulates what
``auth_middleware`` later does — sets ``request.state.user``) so we can
exercise the middleware in isolation from the rest of the orchestrator.

The middleware's ``user_id`` / ``mcp_token_id`` binding is unit-tested
in ``test_logging_config.py::TestBindRequestUser`` against the binder
processor directly; here we assert the integration: that a real
TestClient request causes those values to appear on a structlog event
emitted from inside the route handler.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import structlog
from correlation import correlation_middleware
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient


def _build_app(
    *,
    set_user_id: Optional[str] = None,
    set_mcp_token_id: Optional[str] = None,
    set_mcp_user_id: Optional[str] = None,
) -> FastAPI:
    """Tiny FastAPI app with just the correlation middleware + one route.

    The route optionally pre-populates ``request.state`` with values
    that ``auth_middleware`` would normally set in the real app, then
    emits a structured log line so tests can assert the binder
    processor picked them up.
    """
    app = FastAPI()
    app.middleware("http")(correlation_middleware)

    log = structlog.get_logger("test.correlation")

    @app.get("/log")
    async def _log_endpoint(request: Request):
        # Simulate what auth_middleware does in the real stack.
        if set_user_id is not None:

            class _U:
                pass

            user = _U()
            user.user_id = set_user_id
            request.state.user = user
        if set_mcp_token_id is not None:
            request.state.mcp_token_id = set_mcp_token_id
        if set_mcp_user_id is not None:
            request.state.mcp_user_id = set_mcp_user_id
        log.info("hello.from.endpoint")
        return {"request_id": getattr(request.state, "request_id", None)}

    return app


# ---------------------------------------------------------------------------
# X-Request-ID header behavior
# ---------------------------------------------------------------------------


class TestRequestId:
    def test_generates_request_id_when_header_absent(self):
        app = _build_app()
        client = TestClient(app)
        r = client.get("/log")
        assert r.status_code == 200
        rid = r.headers.get("X-Request-ID")
        assert rid is not None and len(rid) == 32
        # uuid4().hex is 32 lowercase hex chars.
        assert re.fullmatch(r"[0-9a-f]{32}", rid), (
            f"Expected uuid4().hex (32 lowercase hex chars), got {rid!r}"
        )

    def test_echoes_request_id_when_header_present(self):
        app = _build_app()
        client = TestClient(app)
        r = client.get("/log", headers={"X-Request-ID": "custom-id-123"})
        assert r.status_code == 200
        assert r.headers["X-Request-ID"] == "custom-id-123"

    def test_strips_whitespace_from_incoming_header(self):
        app = _build_app()
        client = TestClient(app)
        r = client.get("/log", headers={"X-Request-ID": "  custom-id-456  "})
        assert r.status_code == 200
        assert r.headers["X-Request-ID"] == "custom-id-456"

    def test_blank_header_falls_back_to_generated_id(self):
        app = _build_app()
        client = TestClient(app)
        r = client.get("/log", headers={"X-Request-ID": "   "})
        assert r.status_code == 200
        rid = r.headers["X-Request-ID"]
        assert re.fullmatch(r"[0-9a-f]{32}", rid)

    def test_request_state_request_id_matches_response_header(self):
        app = _build_app()
        client = TestClient(app)
        r = client.get("/log", headers={"X-Request-ID": "k9"})
        assert r.status_code == 200
        assert r.json()["request_id"] == "k9"
        assert r.headers["X-Request-ID"] == "k9"

    def test_each_request_gets_a_distinct_generated_id(self):
        app = _build_app()
        client = TestClient(app)
        ids = {client.get("/log").headers["X-Request-ID"] for _ in range(5)}
        assert len(ids) == 5, "each request must get its own request_id"


# ---------------------------------------------------------------------------
# Correlation appears in emitted log records
# ---------------------------------------------------------------------------


class _CapturingHandler(logging.Handler):
    """Stdlib handler that records every emitted record for inspection."""

    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _attach_capture() -> _CapturingHandler:
    """Attach a capturing handler to the root logger.

    Used instead of ``capture_logs`` because we need to verify that the
    bound contextvars / request.state binding actually flows through the
    real configured pipeline, not the bypass chain that ``capture_logs``
    installs.
    """
    handler = _CapturingHandler()
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(handler)
    return handler


def _detach_capture(handler: _CapturingHandler) -> None:
    logging.getLogger().removeHandler(handler)


class TestCorrelationVisibleInLogs:
    def test_request_id_appears_on_log_record_during_request(self):
        handler = _attach_capture()
        try:
            app = _build_app()
            client = TestClient(app)
            r = client.get("/log", headers={"X-Request-ID": "rid-corr-1"})
            assert r.status_code == 200
        finally:
            _detach_capture(handler)

        # The structured log line ends up as a stdlib LogRecord whose
        # rendered message (after ProcessorFormatter) embeds the bound
        # contextvars. The handler captured the *unrendered* record;
        # render it the same way the formatter would.
        endpoint_records = [
            rec for rec in handler.records if "hello.from.endpoint" in rec.getMessage()
        ]
        assert endpoint_records, (
            "Expected at least one captured record from hello.from.endpoint; "
            f"got {[r.getMessage() for r in handler.records]!r}"
        )
        rendered = endpoint_records[0].getMessage()
        assert "rid-corr-1" in rendered, (
            f"Expected request_id 'rid-corr-1' in rendered log message; got: {rendered!r}"
        )

    def test_user_id_bound_when_state_user_set_by_route(self):
        handler = _attach_capture()
        try:
            app = _build_app(set_user_id="alice")
            client = TestClient(app)
            r = client.get("/log", headers={"X-Request-ID": "rid-u-1"})
            assert r.status_code == 200
        finally:
            _detach_capture(handler)

        endpoint_records = [
            rec for rec in handler.records if "hello.from.endpoint" in rec.getMessage()
        ]
        assert endpoint_records
        rendered = endpoint_records[0].getMessage()
        assert "alice" in rendered, (
            f"Expected user_id 'alice' in rendered log message; got: {rendered!r}"
        )

    def test_mcp_token_id_bound_when_state_set_by_route(self):
        handler = _attach_capture()
        try:
            app = _build_app(
                set_mcp_token_id="tok_xyz",
                set_mcp_user_id="alice_mcp",
            )
            client = TestClient(app)
            r = client.get("/log", headers={"X-Request-ID": "rid-mcp-1"})
            assert r.status_code == 200
        finally:
            _detach_capture(handler)

        endpoint_records = [
            rec for rec in handler.records if "hello.from.endpoint" in rec.getMessage()
        ]
        assert endpoint_records
        rendered = endpoint_records[0].getMessage()
        assert "tok_xyz" in rendered
        assert "alice_mcp" in rendered


# ---------------------------------------------------------------------------
# Existing stdlib logger (`logging.getLogger`) call sites still work
# ---------------------------------------------------------------------------


class TestStdlibBridge:
    def test_caplog_sees_stdlib_log_calls_during_request(self, caplog):
        """The stdlib bridge must keep `caplog` working for legacy call sites.

        Hundreds of orchestrator modules use ``_log = logging.getLogger(__name__)``;
        the structured-logging chunk must NOT break tests that capture
        those via pytest's built-in ``caplog`` fixture.
        """
        app = FastAPI()
        app.middleware("http")(correlation_middleware)

        @app.get("/legacy")
        async def _legacy_endpoint():
            logging.getLogger("test.legacy").warning("hello-from-stdlib")
            return {"ok": True}

        client = TestClient(app)
        with caplog.at_level(logging.WARNING):
            r = client.get("/legacy")

        assert r.status_code == 200
        # Confirm caplog received the stdlib record.
        captured_messages = [rec.getMessage() for rec in caplog.records]
        assert any("hello-from-stdlib" in m for m in captured_messages), (
            f"caplog must capture stdlib log calls when the structlog "
            f"bridge is active; got messages: {captured_messages!r}"
        )
