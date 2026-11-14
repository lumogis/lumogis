# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-816 — a full connection pool is 503, not 500.

`PoolExhaustedError` is a `RuntimeError`, so without a handler it reaches the
client as a bare 500 after the caller has already waited out the ten-second
checkout deadline. That is the wrong thing to tell them: 500 says the server is
broken, when the database is healthy and the same request will succeed shortly.
"""

from __future__ import annotations

import json

from adapters.postgres_store import PoolExhaustedError


def _handler():
    import main

    for exc_type, handler in main.app.exception_handlers.items():
        if exc_type is PoolExhaustedError:
            return handler
    return None


def test_pool_exhaustion_is_registered_as_its_own_response():
    """Registration is half the fix — an unregistered handler is dead code."""
    assert _handler() is not None, "PoolExhaustedError falls through to a bare 500"


def test_pool_exhaustion_answers_503_with_retry_after():
    """Busy, not broken.

    `Retry-After` matters because the caller has *already* waited the full
    checkout deadline; without it a client cannot tell this from a fault and
    has no basis for backing off.
    """
    from fastapi import Request

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/whatever",
            "headers": [],
            "query_string": b"",
        }
    )
    response = _handler()(request, PoolExhaustedError("no connection free"))

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert "busy" in json.loads(response.body)["detail"].lower()
