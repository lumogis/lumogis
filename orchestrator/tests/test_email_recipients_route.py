# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""HTTP contract for ``GET /api/v1/email/recipients`` (LUM-818).

Two of these carry decisions rather than plumbing:

* **400, not an empty list**, for a too-short query. `[]` would read as "you
  have no such correspondent", which for a one-character query is a lie.
* **No `user_id` parameter exists, and none may be added.** The response is a
  list of a person's correspondents; a caller-supplied identity would make this
  a read of someone else's address book. The test asserts the absence.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from routes.api_v1 import email_recipients as route_mod

import config
from services import email_recipients as svc


class _Store:
    def fetch_all(self, sql, params=None):
        if "email_sender_rule" in sql:
            return []
        if "FROM signals" in sql:
            return [
                {
                    "addr": "jane@example.com",
                    "display": "Jane Smith",
                    "last_at": None,
                    "n": 2,
                }
            ]
        if "email_sent_log" in sql:
            return []
        raise AssertionError(f"unexpected SQL: {sql!r}")

    def fetch_one(self, sql, params=None):
        return None

    def execute(self, sql, params=None):
        return None


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store())
    route_mod._limiter.check.__self__._store.clear()  # isolate the rate bucket
    from main import app

    return TestClient(app)


def test_a_match_is_returned_with_its_display_name(client):
    r = client.get("/api/v1/email/recipients", params={"q": "jane"})

    assert r.status_code == 200
    suggestions = r.json()["suggestions"]
    assert suggestions[0]["address"] == "jane@example.com"
    assert suggestions[0]["display_name"] == "Jane Smith"
    assert suggestions[0]["corresponded"] is False, "received-from only, never written to"


@pytest.mark.parametrize("bad", ["", "a"])
def test_a_too_short_query_is_refused_not_answered(client, bad):
    r = client.get("/api/v1/email/recipients", params={"q": bad})

    assert r.status_code in (400, 422), f"{bad!r} should be refused, got {r.status_code}"


def test_the_endpoint_takes_no_user_id_parameter():
    """The identity comes from the session, never from the wire.

    A ``user_id`` argument here would turn a list of one person's
    correspondents into a read of somebody else's. Asserted on the handler's
    own signature rather than by probing the app's route table — the signature
    is what a future contributor edits, and it cannot drift from what ships.
    """
    import inspect

    params = set(inspect.signature(route_mod.suggest_recipients).parameters)

    assert "user_id" not in params
    assert params == {"request", "q", "limit", "_"}


def test_a_user_id_query_argument_is_ignored_rather_than_honoured(client):
    """Belt and braces: even if someone sends one, it must not be read."""
    r = client.get("/api/v1/email/recipients", params={"q": "jane", "user_id": "somebody-else"})

    assert r.status_code == 200
    assert r.json()["suggestions"][0]["address"] == "jane@example.com"


def test_the_limit_is_capped_at_the_service_maximum(client):
    r = client.get("/api/v1/email/recipients", params={"q": "jane", "limit": 999})

    assert r.status_code == 422, f"limit must be bounded at {svc.MAX_LIMIT}"


def test_rate_limited_requests_get_429(client):
    """Bound to the configured bucket, not a copied number.

    The limit is a tuning knob -- it moved 60 -> 90 during review to stop a fast
    typist hitting 429 mid-compose. A hardcoded loop turns every future tuning
    change into a spurious test failure, and tempts whoever hits it to "fix" the
    test rather than think about the limit.
    """
    limit = route_mod._limiter._max

    for _ in range(limit):
        client.get("/api/v1/email/recipients", params={"q": "jane"})

    r = client.get("/api/v1/email/recipients", params={"q": "jane"})

    assert r.status_code == 429
