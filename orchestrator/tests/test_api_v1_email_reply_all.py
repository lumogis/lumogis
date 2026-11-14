# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``POST /api/v1/email/reply-all`` (LUM-705).

The load-bearing case is the **UIDVALIDITY degrade**. A reset means the server
reassigned UIDs, so the stored uid identifies a *different message* — and using
its headers would put a stranger's thread participants into this user's Cc field.
LUM-695's `/code-review` found exactly this defect on the attachment read path;
this is the same class in a new place.

The second is that a header miss is a **200 with ``degraded``**, never a 4xx. The
signal is the caller's own and the request is well-formed; it is the mailbox that
did not answer, and a 404 would be indistinguishable from "not your signal".
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routes.api_v1 import email_send as mod

import config

SIGNAL = "11111111-1111-1111-1111-111111111111"
ME = "me@example.com"
SENDER = "sender@example.com"

_HEADERS = {
    "Subject": "Re: school trip",
    "Message-ID": "<orig@school.edu>",
    "To": f'{ME}, "Doe, Jane" <jane@example.com>',
    "Cc": "teacher@school.edu, head@school.edu",
}


class _Source:
    id = "src-A"


class _Store:
    def fetch_one(self, sql, params=None):
        if "from_addr" in sql:
            return {"from_addr": SENDER}
        return None

    def fetch_all(self, sql, params=None):
        return []

    def execute(self, sql, params=None):
        return None


@pytest.fixture
def client(monkeypatch):
    from auth import UserContext

    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store())
    monkeypatch.setattr(
        mod, "_assert_owned_signal", lambda sid, uid: mod.OwnedSignal(_Source(), 42, 7)
    )
    from services import email_credentials

    monkeypatch.setattr(email_credentials, "mailbox_account_address", lambda uid, *, instance: ME)
    ctx = UserContext(user_id="u1", role="member", allows_shared=True)
    app = FastAPI()
    app.include_router(mod.router)
    app.dependency_overrides[mod.require_user] = lambda: ctx
    monkeypatch.setattr(mod, "get_user", lambda request: ctx)
    mod._reply_all_calls.clear()
    return TestClient(app)


def _wire_headers(monkeypatch, headers):
    from actions.handlers import smtp_send

    monkeypatch.setattr(smtp_send, "fetch_original_headers", lambda s, uv, uid: headers)


def test_returns_to_and_cc_for_the_callers_own_signal(client, monkeypatch):
    _wire_headers(monkeypatch, _HEADERS)

    body = client.post("/api/v1/email/reply-all", json={"signal_id": SIGNAL}).json()

    assert body["to"] == [SENDER]
    assert body["degraded"] is False
    assert "teacher@school.edu" in body["cc"]
    assert "head@school.edu" in body["cc"]
    # BARE, not the display form — see test_every_returned_address_is_accepted_by_the_send_path.
    assert "jane@example.com" in body["cc"]


def test_the_users_own_address_is_filtered_out(client, monkeypatch):
    _wire_headers(monkeypatch, _HEADERS)

    body = client.post("/api/v1/email/reply-all", json={"signal_id": SIGNAL}).json()

    assert all(ME not in c for c in body["cc"])
    assert body["dropped_self"] is True


def test_the_sender_is_not_duplicated_into_cc(client, monkeypatch):
    _wire_headers(monkeypatch, {**_HEADERS, "To": ME, "Cc": f"{SENDER}, teacher@school.edu"})

    body = client.post("/api/v1/email/reply-all", json={"signal_id": SIGNAL}).json()

    assert body["cc"] == ["teacher@school.edu"]
    assert body["to"] == [SENDER]


def test_a_uidvalidity_mismatch_degrades_instead_of_using_the_headers(client, monkeypatch):
    """The whole reason the shared helper exists.

    ``fetch_original_headers`` returns None on a mismatch, so the route must never
    see — let alone use — headers belonging to whatever message now holds that uid.
    """
    from actions.handlers import smtp_send

    # The real helper, with an adapter reporting a DIFFERENT uidvalidity.
    class _Adapter:
        def __init__(self, source):
            pass

        def fetch_thread_headers(self, uid):
            return (99, _HEADERS)  # stored was 42

    monkeypatch.setattr("adapters.email_imap_source.EmailImapSource", _Adapter)
    assert smtp_send.fetch_original_headers(_Source(), 42, 7) is None

    body = client.post("/api/v1/email/reply-all", json={"signal_id": SIGNAL}).json()

    assert body["degraded"] is True
    assert body["cc"] == [], "a stranger's participants must never reach the Cc"


def test_imap_unreachable_degrades_with_a_200_not_a_5xx(client, monkeypatch):
    _wire_headers(monkeypatch, None)

    r = client.post("/api/v1/email/reply-all", json={"signal_id": SIGNAL})

    assert r.status_code == 200
    assert r.json()["degraded"] is True


def test_degraded_always_carries_an_empty_cc_and_the_sender_in_to(client, monkeypatch):
    """So a client that ignores the flag still behaves as a plain Reply."""
    _wire_headers(monkeypatch, None)

    body = client.post("/api/v1/email/reply-all", json={"signal_id": SIGNAL}).json()

    assert body["cc"] == []
    assert body["to"] == [SENDER]


def test_an_original_with_no_other_recipients_is_not_a_degrade(client, monkeypatch):
    _wire_headers(monkeypatch, {"Subject": "hi", "Message-ID": "<a@x>"})

    body = client.post("/api/v1/email/reply-all", json={"signal_id": SIGNAL}).json()

    assert body["cc"] == []
    assert body["degraded"] is False, "nothing went wrong — there was simply nobody else"


def test_an_unresolvable_own_address_still_returns_the_cc(client, monkeypatch):
    """Degrading the whole feature because we could not learn our own address
    would be worse than showing a Cc containing the user — which is one visible,
    editable line to delete."""
    from services import email_credentials

    def _boom(uid, *, instance):
        raise RuntimeError("credential store down")

    monkeypatch.setattr(email_credentials, "mailbox_account_address", _boom)
    _wire_headers(monkeypatch, _HEADERS)

    body = client.post("/api/v1/email/reply-all", json={"signal_id": SIGNAL}).json()

    assert body["degraded"] is False
    assert "teacher@school.edu" in body["cc"]


def test_large_thread_is_flagged(client, monkeypatch):
    many = ", ".join(f"u{i}@x.com" for i in range(30))
    _wire_headers(monkeypatch, {**_HEADERS, "Cc": many})

    body = client.post("/api/v1/email/reply-all", json={"signal_id": SIGNAL}).json()

    assert body["large_thread"] is True
    assert body["truncated"] == 0


def test_over_the_bucket_is_429_with_retry_after(client, monkeypatch):
    _wire_headers(monkeypatch, _HEADERS)

    last = None
    for _ in range(mod._REPLY_ALL_LIMIT + 1):
        last = client.post("/api/v1/email/reply-all", json={"signal_id": SIGNAL})

    assert last is not None and last.status_code == 429
    assert last.headers.get("Retry-After") == "60"


def test_the_reply_all_bucket_is_separate_from_quote_and_draft(client, monkeypatch):
    """Exhausting one must not starve the others — the reason /quote took its own."""
    _wire_headers(monkeypatch, _HEADERS)

    for _ in range(mod._REPLY_ALL_LIMIT + 1):
        client.post("/api/v1/email/reply-all", json={"signal_id": SIGNAL})

    assert len(mod._reply_all_calls["u1"]) >= mod._REPLY_ALL_LIMIT
    assert len(mod._quote_calls["u1"]) == 0
    assert len(mod._draft_calls["u1"]) == 0


def test_another_users_signal_is_404_not_403(monkeypatch):
    from auth import UserContext
    from fastapi import HTTPException

    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store())

    def _deny(sid, uid):
        raise HTTPException(404, detail={"error": "not_found"})

    monkeypatch.setattr(mod, "_assert_owned_signal", _deny)
    ctx = UserContext(user_id="u1", role="member", allows_shared=True)
    app = FastAPI()
    app.include_router(mod.router)
    app.dependency_overrides[mod.require_user] = lambda: ctx
    monkeypatch.setattr(mod, "get_user", lambda request: ctx)
    mod._reply_all_calls.clear()

    r = TestClient(app).post("/api/v1/email/reply-all", json={"signal_id": SIGNAL})

    assert r.status_code == 404, "403 would confirm another user's signal exists"


def test_reply_all_still_drops_self_after_narrowing(monkeypatch):
    """An unresolvable mailbox host must not cost the user their own Cc filter.

    LUM-831 Pass 0. This test deliberately does **not** use the ``client``
    fixture, because that fixture monkeypatches ``mailbox_account_address``
    away — and the accessor is exactly what is under test here.

    Before LUM-831 narrowed it, the accessor went through ``load_connection``,
    which resolves the host live (``_connection_from_payload`` →
    ``_resolve_host_ips``). A mailbox whose host stopped resolving therefore
    raised, the route's ``except Exception`` set ``own_addr = ""``, and the user
    was left in their own Cc — a wrong Cc caused by DNS. The username never
    needed the host: it is read from the decrypted payload above every one of
    those steps.
    """
    from auth import UserContext
    from connectors.registry import IMAP

    from services import connector_credentials as ccs
    from services import email_credentials as emc

    monkeypatch.setattr(config, "get_metadata_store", lambda: _Store())
    monkeypatch.setattr(
        mod, "_assert_owned_signal", lambda sid, uid: mod.OwnedSignal(_Source(), 42, 7)
    )

    def _payload(user_id, connector, *, instance=""):
        if connector != IMAP:
            return None
        return {
            "host": "imap.example.com",
            "port": 993,
            "username": ME,
            "password": "unused-by-this-path",
            "use_ssl": True,
            "auth_type": "app_password",
        }

    monkeypatch.setattr(ccs, "get_payload", _payload)

    def _no_dns(host):
        raise OSError("name or service not known")

    monkeypatch.setattr(emc._ohu, "_resolved_ips", _no_dns)

    ctx = UserContext(user_id="u1", role="member", allows_shared=True)
    app = FastAPI()
    app.include_router(mod.router)
    app.dependency_overrides[mod.require_user] = lambda: ctx
    monkeypatch.setattr(mod, "get_user", lambda request: ctx)
    mod._reply_all_calls.clear()
    c = TestClient(app)

    _wire_headers(monkeypatch, _HEADERS)
    body = c.post("/api/v1/email/reply-all", json={"signal_id": SIGNAL}).json()

    assert body["dropped_self"] is True, (
        "the caller was left in their own Cc because the mailbox host did not "
        "resolve — reading a username must not depend on DNS"
    )
    assert ME not in body["cc"]
