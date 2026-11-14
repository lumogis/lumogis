# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route + framework-composition tests for the proposal producer (LUM-623).

The composition tests deliberately use the **real** ``permissions.check_permission``
(no monkeypatch) so they catch the ASK-mode gaps both critique rounds hunted:
an approved proposal must execute under ASK, and — the Sonnet R2 finding — the
*reverse* path must too.
"""

from __future__ import annotations

import pytest
from actions.errors import PermanentActionError
from actions.executor import execute
from actions.registry import register_action
from fastapi.testclient import TestClient
from models.actions import ActionResult
from models.actions import ActionSpec
from tests.test_action_proposals_producer import FakeStore
from tests.test_action_proposals_producer import _Payload

import config


# --------------------------------------------------------------------------- #
# Routes (auth disabled → dev user "default")
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _auth_disabled(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")


@pytest.fixture
def store():
    s = FakeStore()
    config._instances["metadata_store"] = s
    register_action(
        ActionSpec(
            name="_t_route_ok",
            connector="_t",
            action_type="test_write",
            is_write=True,
            is_reversible=True,
            handler=lambda inp, **kw: None,
            input_model=_Payload,
        )
    )
    yield s
    config._instances.pop("metadata_store", None)


@pytest.fixture
def client(store):
    import main

    with TestClient(main.app) as c:
        yield c


def test_create_route_201_pending(client):
    r = client.post(
        "/api/v1/approvals/proposals",
        json={"action_name": "_t_route_ok", "payload": {"thing": "x"}},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "pending" and isinstance(body["proposal_id"], int)


def test_approve_and_list_routes(client):
    pid = client.post(
        "/api/v1/approvals/proposals",
        json={"action_name": "_t_route_ok", "payload": {"thing": "y"}},
    ).json()["proposal_id"]

    listed = client.get("/api/v1/approvals/proposals").json()["proposals"]
    assert any(p["proposal_id"] == pid for p in listed)

    r = client.post(f"/api/v1/approvals/proposals/{pid}/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved"


def test_create_route_rejects_smuggled_field_422(client):
    r = client.post(
        "/api/v1/approvals/proposals",
        json={"action_name": "_t_route_ok", "payload": {"thing": "x", "user_id": "victim"}},
    )
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# LUM-682: cancel an abandoned pending proposal (pending → rejected)
# --------------------------------------------------------------------------- #
def test_cancel_route_pending_to_rejected(client):
    pid = client.post(
        "/api/v1/approvals/proposals",
        json={"action_name": "_t_route_ok", "payload": {"thing": "z"}},
    ).json()["proposal_id"]
    r = client.post(f"/api/v1/approvals/proposals/{pid}/cancel")
    assert r.status_code == 200
    body = r.json()
    assert body["proposal_id"] == pid and body["status"] == "rejected"
    # It leaves the pending list.
    listed = client.get("/api/v1/approvals/proposals").json()["proposals"]
    assert all(p["proposal_id"] != pid for p in listed)


def test_cancel_route_non_pending_404(client):
    pid = client.post(
        "/api/v1/approvals/proposals",
        json={"action_name": "_t_route_ok", "payload": {"thing": "a"}},
    ).json()["proposal_id"]
    client.post(f"/api/v1/approvals/proposals/{pid}/approve")  # now approved
    # Cannot cancel a non-pending proposal — 404, no oracle.
    r = client.post(f"/api/v1/approvals/proposals/{pid}/cancel")
    assert r.status_code == 404


def test_cancel_route_unknown_404(client):
    r = client.post("/api/v1/approvals/proposals/999999/cancel")
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# Framework composition — approved satisfies ASK (real check_permission)
# --------------------------------------------------------------------------- #
class _PermissiveStore:
    """No connector_permissions rows → default ASK; audit/log writes no-op."""

    def fetch_one(self, *a, **k):
        return None

    def fetch_all(self, *a, **k):
        return []

    def execute(self, *a, **k):
        return None


@pytest.fixture
def perm_store():
    s = _PermissiveStore()
    config._instances["metadata_store"] = s
    yield s
    config._instances.pop("metadata_store", None)


def _register_write(name, handler, *, reversible=False, reverse_name=None):
    register_action(
        ActionSpec(
            name=name,
            connector="askconn",  # no permission row → ASK
            action_type="email_writeback",  # not hard-limited
            is_write=True,
            is_reversible=reversible,
            handler=handler,
            reverse_action_name=reverse_name,
        )
    )


def test_unapproved_write_denied_under_ask(perm_store):
    _register_write("_t_ask_denied", lambda inp, **kw: ActionResult(success=True, output="ok"))
    res = execute("_t_ask_denied", {}, user_id="u1", approved=False)
    assert res.success is False and "requires DO mode" in (res.error or "")


def test_approved_write_executes_under_ask(perm_store):
    _register_write("_t_ask_ok", lambda inp, **kw: ActionResult(success=True, output="ran"))
    res = execute("_t_ask_ok", {}, user_id="u1", approved=True)
    assert res.success is True and res.output == "ran"


def test_reverse_path_threads_approved_true(perm_store, monkeypatch):
    # The reverse path must call execute(..., approved=True) — else undo is
    # denied under the default ASK mode (Sonnet R2-1).
    captured = {}

    def fake_execute(name, inp=None, *, user_id, approved=False):
        captured["approved"] = approved
        return ActionResult(success=True, output="reversed")

    import actions.reversibility as rev

    # attempt_reverse does a local `from actions.executor import execute`, so
    # patch the source module, not the reversibility namespace.
    monkeypatch.setattr("actions.executor.execute", fake_execute)

    class _AuditStore:
        def fetch_one(self, *a, **k):
            return {
                "id": 1,
                "action_name": "imap_move",
                "connector": "imap",
                "reverse_action": {
                    "action_name": "imap_writeback_reverse",
                    "input": {"op": "move"},
                },
                "reversed_at": None,
            }

        def execute(self, *a, **k):
            return None

    config._instances["metadata_store"] = _AuditStore()
    try:
        monkeypatch.setattr(rev, "mark_reversed", lambda token: None)
        rev.attempt_reverse("tok-123", user_id="u1")
    finally:
        config._instances.pop("metadata_store", None)
    assert captured["approved"] is True


# --------------------------------------------------------------------------- #
# Failure disposal at the composition level (Sonnet R2-2; rewritten for LUM-762)
#
# This is the only test that proves disposal through `_finish_claim` itself rather
# than through the queue module's fake. Before LUM-762 it asserted that a PERMANENT
# failure dead-lettered while a non-permanent one would have taken `fail_execution`.
# That fork no longer exists — both converge on `dead_letter` — so the test now
# proves the CONVERGENCE, which is the property the chunk actually introduces.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("action_name", "handler", "expect_permanent"),
    [
        (
            "_t_permanent",
            lambda inp, **kw: (_ for _ in ()).throw(PermanentActionError("stale", "gone")),
            True,
        ),
        (
            "_t_transient",
            lambda inp, **kw: (_ for _ in ()).throw(RuntimeError("transient boom")),
            False,
        ),
    ],
)
def test_every_failure_kind_dead_letters(
    perm_store, monkeypatch, action_name, handler, expect_permanent
):
    _register_write(action_name, handler)

    calls = {"dead": 0}

    from actions import proposal_execute

    from services import proposal_queue

    def _rec_dead(pid, err):
        calls["dead"] += 1

    monkeypatch.setattr(proposal_queue, "dead_letter", _rec_dead)
    # No `fail_execution` monkeypatch: the symbol is gone (LUM-762), and
    # `monkeypatch.setattr` on a missing attribute raises AttributeError.
    assert not hasattr(proposal_queue, "fail_execution")

    claimed = proposal_queue.ClaimedProposal(
        id=7,
        user_id="u1",
        action_name=action_name,
        payload={},
        attempt=0,
        created_at=__import__("datetime").datetime.now(),
        status="executing",
    )
    res = proposal_execute._finish_claim(claimed, user_id="u1")
    assert res.success is False
    assert res.permanent is expect_permanent, "the flag still selects the USER-FACING CODE"
    assert calls["dead"] == 1, "one disposal, whatever the failure kind"
