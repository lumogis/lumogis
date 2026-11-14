# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route-level tests for the LUM-652 send surface — asserted through the real
FastAPI routes (TestClient), not the service layer, so they catch a route that
drops ``code`` / forgets to surface ``first_contact``.

* ``POST /api/v1/email/reply-draft`` — owner-scoped 404 + separate-bucket 429.
* ``POST /api/v1/approvals/proposals`` — ``first_contact`` on the create response.
* ``POST /api/v1/approvals/proposals/{id}/execute`` — machine ``code`` on the wire.
"""

from __future__ import annotations

import json
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from actions import registry as actions_registry
from actions.errors import PermanentActionError
from actions.registry import register_action
from fastapi.testclient import TestClient
from models.actions import ActionResult
from models.actions import ActionSpec
from pydantic import BaseModel
from pydantic import ConfigDict
from tests.fakes.fake_proposal_queue_store import FakeProposalQueueStore

import config


@pytest.fixture(autouse=True)
def _auth_disabled(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")


@pytest.fixture(autouse=True)
def _registry_cleanup():
    snapshot = dict(actions_registry._registry)
    yield
    actions_registry._registry.clear()
    actions_registry._registry.update(snapshot)


@pytest.fixture(autouse=True)
def _reset_buckets():
    from routes.api_v1.approvals import _approval_calls
    from routes.api_v1.email_send import _draft_calls
    from routes.api_v1.email_send import _quote_calls
    from routes.api_v1.email_send import _refine_calls

    for bucket in (_approval_calls, _draft_calls, _refine_calls, _quote_calls):
        bucket.clear()
    yield
    for bucket in (_approval_calls, _draft_calls, _refine_calls, _quote_calls):
        bucket.clear()


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thing: str


# --------------------------------------------------------------------------- #
# reply-draft route: owner-scope + rate limit
# --------------------------------------------------------------------------- #
def test_reply_draft_404_for_non_owned(client, monkeypatch):
    monkeypatch.setattr("actions.handlers.imap_writeback._signal_url", lambda sid: "imap://x/1/2")
    monkeypatch.setattr(
        "actions.handlers.imap_writeback._parse_imap_url", lambda url: ("src", 1, 2)
    )

    def _deny(sid, uid):
        raise PermanentActionError("not_found", "not owned")

    monkeypatch.setattr("actions.handlers.imap_writeback._load_owned_source", _deny)

    r = client.post("/api/v1/email/reply-draft", json={"signal_id": "sig-x"})
    assert r.status_code == 404


def test_reply_draft_rate_limited(client, monkeypatch):
    # Owned + a blank draft so each call is cheap; the 21st in the window → 429.
    monkeypatch.setattr("actions.handlers.imap_writeback._signal_url", lambda sid: "imap://x/1/2")
    monkeypatch.setattr(
        "actions.handlers.imap_writeback._parse_imap_url", lambda url: ("src", 1, 2)
    )
    monkeypatch.setattr(
        "actions.handlers.imap_writeback._load_owned_source", lambda sid, uid: object()
    )
    monkeypatch.setattr(
        "services.email_reply_draft.suggest_reply_draft", lambda sid, *, user_id: ""
    )

    codes = [
        client.post("/api/v1/email/reply-draft", json={"signal_id": "s"}).status_code
        for _ in range(21)
    ]
    assert codes[:20] == [200] * 20
    assert codes[20] == 429


# --------------------------------------------------------------------------- #
# LUM-698 — POST /api/v1/email/quote
# --------------------------------------------------------------------------- #
def _own(monkeypatch):
    monkeypatch.setattr("actions.handlers.imap_writeback._signal_url", lambda sid: "imap://x/1/2")
    monkeypatch.setattr(
        "actions.handlers.imap_writeback._parse_imap_url", lambda url: ("src", 1, 2)
    )
    monkeypatch.setattr(
        "actions.handlers.imap_writeback._load_owned_source", lambda sid, uid: object()
    )


def test_quote_404_for_non_owned_signal(client, monkeypatch):
    monkeypatch.setattr("actions.handlers.imap_writeback._signal_url", lambda sid: "imap://x/1/2")
    monkeypatch.setattr(
        "actions.handlers.imap_writeback._parse_imap_url", lambda url: ("src", 1, 2)
    )

    def _deny(sid, uid):
        raise PermanentActionError("not_found", "not owned")

    monkeypatch.setattr("actions.handlers.imap_writeback._load_owned_source", _deny)

    r = client.post("/api/v1/email/quote", json={"signal_id": "sig-x"})
    assert r.status_code == 404


def test_quote_returns_blank_for_owned_signal_with_no_body(client, monkeypatch):
    _own(monkeypatch)
    monkeypatch.setattr("services.email_quote.build_quote", lambda sid, *, user_id, style: "")
    r = client.post("/api/v1/email/quote", json={"signal_id": "s"})
    assert r.status_code == 200
    assert r.json()["quote"] == ""


def test_quote_passes_style_through(client, monkeypatch):
    _own(monkeypatch)
    seen = {}

    def _build(sid, *, user_id, style):
        seen["style"] = style
        return "QUOTED"

    monkeypatch.setattr("services.email_quote.build_quote", _build)
    r = client.post("/api/v1/email/quote", json={"signal_id": "s", "style": "forward"})
    assert r.status_code == 200
    assert r.json()["quote"] == "QUOTED"
    assert seen["style"] == "forward"


def test_quote_rejects_unknown_style(client, monkeypatch):
    _own(monkeypatch)
    r = client.post("/api/v1/email/quote", json={"signal_id": "s", "style": "nope"})
    assert r.status_code == 422


def test_quote_uses_its_own_rate_bucket(client, monkeypatch):
    """Exhausting the quote bucket must NOT 429 the reply-draft flow — a bounded
    IMAP fetch must not be able to starve the LLM/send budgets (or vice versa)."""
    _own(monkeypatch)
    monkeypatch.setattr("services.email_quote.build_quote", lambda sid, *, user_id, style: "")
    monkeypatch.setattr(
        "services.email_reply_draft.suggest_reply_draft", lambda sid, *, user_id: ""
    )

    codes = [
        client.post("/api/v1/email/quote", json={"signal_id": "s"}).status_code for _ in range(21)
    ]
    assert codes[:20] == [200] * 20
    assert codes[20] == 429
    # The sibling bucket is untouched.
    assert client.post("/api/v1/email/reply-draft", json={"signal_id": "s"}).status_code == 200


# --------------------------------------------------------------------------- #
# create route: first_contact on the response
# --------------------------------------------------------------------------- #
class _AnnStore:
    """Stores the annotation on INSERT and returns it from get_proposal — the
    minimal shape the create route's read-back needs."""

    def __init__(self):
        self.rows: dict[int, dict] = {}
        self._seq = 0

    def ping(self):
        return True

    def close(self):
        pass

    def fetch_one(self, sql, params=None):
        q = " ".join(sql.split()).lower()
        p = params or ()
        if q.startswith("insert into action_proposals"):
            self._seq += 1
            self.rows[self._seq] = {
                "id": self._seq,
                "user_id": p[0],
                "action_name": p[1],
                "payload": json.loads(p[2]),
                "annotation": json.loads(p[3]),
                "status": "pending",
            }
            return {"id": self._seq}
        if "payload, annotation from action_proposals" in q:
            pid, uid = int(p[0]), p[1]
            r = self.rows.get(pid)
            if r and r["user_id"] == uid:
                return {
                    "id": r["id"],
                    "status": r["status"],
                    "action_name": r["action_name"],
                    "payload": r["payload"],
                    "annotation": json.dumps(r["annotation"]),
                }
        return None

    def fetch_all(self, sql, params=None):
        return []


def test_create_proposal_response_first_contact(client, monkeypatch):
    monkeypatch.setitem(config._instances, "metadata_store", _AnnStore())
    register_action(
        ActionSpec(
            name="_t_send_fc",
            connector="smtp",
            action_type="test_write",
            is_write=True,
            is_reversible=False,
            handler=lambda inp, **kw: ActionResult(success=True, output="ok"),
            input_model=_Payload,
            annotate=lambda payload, user_id: {"first_contact": True},
        )
    )
    r = client.post(
        "/api/v1/approvals/proposals",
        json={"action_name": "_t_send_fc", "payload": {"thing": "x"}},
    )
    assert r.status_code == 201
    assert r.json()["first_contact"] is True


# --------------------------------------------------------------------------- #
# execute route: machine `code` crosses the wire
# --------------------------------------------------------------------------- #
def test_execute_wire_response_surfaces_code(client, monkeypatch):
    store = FakeProposalQueueStore()
    monkeypatch.setitem(config._instances, "metadata_store", store)
    monkeypatch.setattr("permissions.check_permission", lambda *a, **k: True)

    def _raise(_inp):
        raise PermanentActionError("no_drafts_folder", "server advertises no drafts")

    register_action(
        ActionSpec(
            name="_t_save_draft_fail",
            connector="imap",
            action_type="email_save_draft",
            is_write=True,
            is_reversible=False,
            handler=_raise,
        )
    )
    now = datetime.now(timezone.utc)
    store.rows[1] = {
        "id": 1,
        "user_id": "default",
        "action_name": "_t_save_draft_fail",
        "payload": {},
        "status": "approved",
        "attempt": 0,
        "run_after": now - timedelta(seconds=1),
        "claimed_at": None,
        "claimed_by": None,
        "created_at": now,
        "executed_at": None,
        "finished_at": None,
        "error": None,
    }

    r = client.post("/api/v1/approvals/proposals/1/execute", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["code"] == "no_drafts_folder"  # the machine token crossed the wire


# --------------------------------------------------------------------------- #
# refine route (LUM-684): on-box tone/length transform of the caller's own body
# --------------------------------------------------------------------------- #
def test_refine_unavailable_when_no_model(client, monkeypatch):
    monkeypatch.setattr("services.text_refine.resolve_refine_model", lambda uid: "")
    r = client.post("/api/v1/email/refine", json={"text": "hi", "preset": "formal"})
    assert r.status_code == 200
    body = r.json()
    assert body == {"text": "", "available": False}


def test_refine_transforms_with_preset(client, monkeypatch):
    seen = {}

    def _fake_refine(text, *, instruction, user_id, model):
        seen["text"] = text
        seen["instruction"] = instruction
        seen["model"] = model
        return "REFINED"

    monkeypatch.setattr("services.text_refine.resolve_refine_model", lambda uid: "llama")
    monkeypatch.setattr("services.text_refine.refine_text", _fake_refine)
    r = client.post("/api/v1/email/refine", json={"text": "hello", "preset": "formal"})
    assert r.status_code == 200
    assert r.json() == {"text": "REFINED", "available": True}
    # The server-owned preset instruction reached the service (not the key).
    from services.text_refine import PRESET_INSTRUCTIONS

    assert seen["instruction"] == PRESET_INSTRUCTIONS["formal"]
    assert seen["text"] == "hello"
    assert seen["model"] == "llama"


def test_refine_free_form_instruction(client, monkeypatch):
    seen = {}
    monkeypatch.setattr("services.text_refine.resolve_refine_model", lambda uid: "llama")
    monkeypatch.setattr(
        "services.text_refine.refine_text",
        lambda text, *, instruction, user_id, model: seen.setdefault("i", instruction) or "R",
    )
    r = client.post(
        "/api/v1/email/refine",
        json={"text": "hello", "instruction": "make it apologetic"},
    )
    assert r.status_code == 200
    assert seen["i"] == "make it apologetic"


def test_refine_full_in_budget_body_not_truncated_at_route(client, monkeypatch):
    seen = {}
    big = "x" * 8000  # the request max — a valid body
    monkeypatch.setattr("services.text_refine.resolve_refine_model", lambda uid: "llama")
    monkeypatch.setattr(
        "services.text_refine.refine_text",
        lambda text, *, instruction, user_id, model: seen.setdefault("t", text) or "R",
    )
    r = client.post("/api/v1/email/refine", json={"text": big, "preset": "shorter"})
    assert r.status_code == 200
    assert seen["t"] == big  # the route hands the full body to the service


@pytest.mark.parametrize(
    "payload",
    [
        {"text": "hi"},  # neither preset nor instruction
        {"text": "hi", "preset": "formal", "instruction": "x"},  # both
        {"text": "hi", "preset": "nonsense"},  # unknown preset
        {"text": "", "preset": "formal"},  # empty text
        {"text": "x" * 8001, "preset": "formal"},  # over-long text
        {"text": "hi", "instruction": "y" * 401},  # over-long instruction
    ],
)
def test_refine_validation_422(client, monkeypatch, payload):
    monkeypatch.setattr("services.text_refine.resolve_refine_model", lambda uid: "llama")
    r = client.post("/api/v1/email/refine", json=payload)
    assert r.status_code == 422


def test_refine_rate_limited(client, monkeypatch):
    monkeypatch.setattr("services.text_refine.resolve_refine_model", lambda uid: "llama")
    monkeypatch.setattr(
        "services.text_refine.refine_text",
        lambda text, *, instruction, user_id, model: "R",
    )
    codes = [
        client.post("/api/v1/email/refine", json={"text": "hi", "preset": "clearer"}).status_code
        for _ in range(21)
    ]
    assert codes[:20] == [200] * 20
    assert codes[20] == 429
