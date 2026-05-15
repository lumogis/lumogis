# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``POST /api/v1/approvals/proposals/{id}/execute`` (LUM-123)."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from actions import registry as actions_registry
from actions.executor import ActionResult
from auth import UserContext
from fastapi.testclient import TestClient
from models.actions import ActionSpec
from structlog.testing import capture_logs
from tests.fakes.fake_proposal_queue_store import FakeProposalQueueStore

import config as _config


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_approvals_bucket():
    from routes.api_v1.approvals import _approval_calls

    _approval_calls.clear()
    yield
    _approval_calls.clear()


@pytest.fixture(autouse=True)
def _proposal_execute_registry_cleanup():
    snapshot = dict(actions_registry._registry)
    yield
    actions_registry._registry.clear()
    actions_registry._registry.update(snapshot)


def _stub_noop_registration() -> None:
    actions_registry.register_action(
        ActionSpec(
            name="lumogis.tests.approvals_proposal_execute",
            connector="smtp",
            action_type="draft_email",
            is_write=False,
            is_reversible=False,
            handler=lambda _inp: ActionResult(success=True, output="ok"),
        ),
    )


def test_proposals_execute_200(client, monkeypatch):
    store = FakeProposalQueueStore()
    monkeypatch.setitem(_config._instances, "metadata_store", store)
    monkeypatch.setattr("permissions.check_permission", lambda *a, **k: True)
    _stub_noop_registration()
    now = datetime.now(timezone.utc)
    pid = 1
    store.rows[pid] = {
        "id": pid,
        "user_id": "default",
        "action_name": "lumogis.tests.approvals_proposal_execute",
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

    resp = client.post("/api/v1/approvals/proposals/1/execute", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["output"] == "ok"
    assert store.rows[pid]["status"] == "done"


def test_proposals_execute_400_wrong_state(client, monkeypatch):
    store = FakeProposalQueueStore()
    monkeypatch.setitem(_config._instances, "metadata_store", store)
    now = datetime.now(timezone.utc)
    pid = 2
    store.rows[pid] = {
        "id": pid,
        "user_id": "default",
        "action_name": "lumogis.tests.approvals_proposal_execute",
        "payload": {},
        "status": "pending",
        "attempt": 0,
        "run_after": now,
        "claimed_at": None,
        "claimed_by": None,
        "created_at": now,
        "executed_at": None,
        "finished_at": None,
        "error": None,
    }
    _stub_noop_registration()

    resp = client.post("/api/v1/approvals/proposals/2/execute", json={})
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_proposal_state"


def test_proposals_execute_403_hard_limited(client, monkeypatch):
    store = FakeProposalQueueStore()
    monkeypatch.setitem(_config._instances, "metadata_store", store)
    actions_registry.register_action(
        ActionSpec(
            name="lumogis.tests.hard_blocked",
            connector="payments",
            action_type="financial_transaction",
            is_write=True,
            is_reversible=False,
            handler=lambda _inp: ActionResult(success=True, output="x"),
        ),
    )
    now = datetime.now(timezone.utc)
    pid = 3
    store.rows[pid] = {
        "id": pid,
        "user_id": "default",
        "action_name": "lumogis.tests.hard_blocked",
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

    resp = client.post("/api/v1/approvals/proposals/3/execute", json={})
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "hard_limited_proposal"


def test_proposals_execute_404(client, monkeypatch):
    monkeypatch.setitem(_config._instances, "metadata_store", FakeProposalQueueStore())
    resp = client.post("/api/v1/approvals/proposals/9999/execute", json={})
    assert resp.status_code == 404


def test_proposals_execute_409_lost_claim(client, monkeypatch):
    import routes.api_v1.approvals as apv_mod

    from services import proposal_queue

    monkeypatch.setattr(apv_mod, "_approvals_rate_check", lambda _: None)

    calls = []

    def _stub_claim(pid, uid, wid):
        calls.append(1)
        return None

    monkeypatch.setattr(proposal_queue, "claim_by_id", _stub_claim)
    monkeypatch.setattr(
        proposal_queue,
        "select_proposal_for_user",
        lambda _pid, _uid: {
            "id": _pid,
            "status": "approved",
            "claimed_at": datetime.now(timezone.utc),
            "claimed_by": "other-worker",
            "payload": {},
            "run_after": None,
            "action_name": "x",
        },
    )

    with capture_logs() as cap:
        resp = client.post("/api/v1/approvals/proposals/41/execute", json={})

    assert resp.status_code == 409
    assert any(ev.get("event") == "audit.proposal.lost_claim" for ev in cap)


def test_proposals_execute_503_mark_done_failure(client, monkeypatch):
    store = FakeProposalQueueStore()
    monkeypatch.setitem(_config._instances, "metadata_store", store)
    monkeypatch.setattr("permissions.check_permission", lambda *a, **k: True)
    _stub_noop_registration()

    calls = []

    def _boom(_pid: int) -> None:
        calls.append(True)
        raise RuntimeError("db down")

    monkeypatch.setattr("services.proposal_queue.mark_done", _boom)

    now = datetime.now(timezone.utc)
    pid = 7
    store.rows[pid] = {
        "id": pid,
        "user_id": "default",
        "action_name": "lumogis.tests.approvals_proposal_execute",
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

    resp = client.post("/api/v1/approvals/proposals/7/execute", json={})
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["error"] == "bookkeeping_failed"
    assert detail["proposal_id"] == 7


def test_proposals_execute_as_user_admin(client, monkeypatch):
    import routes.api_v1.approvals as apv_mod

    store = FakeProposalQueueStore()
    monkeypatch.setitem(_config._instances, "metadata_store", store)
    monkeypatch.setattr("permissions.check_permission", lambda *a, **k: True)

    monkeypatch.setattr(
        apv_mod,
        "get_user",
        lambda _req: UserContext(user_id="admin-u", role="admin"),
    )

    _stub_noop_registration()
    now = datetime.now(timezone.utc)
    store.rows[1] = {
        "id": 1,
        "user_id": "alice",
        "action_name": "lumogis.tests.approvals_proposal_execute",
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

    ok = client.post(
        "/api/v1/approvals/proposals/1/execute?as_user=alice",
        json={},
    )
    assert ok.status_code == 200

    store.rows[2] = {
        "id": 2,
        "user_id": "bob",
        "action_name": "lumogis.tests.approvals_proposal_execute",
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

    nf = client.post("/api/v1/approvals/proposals/2/execute?as_user=alice", json={})
    assert nf.status_code == 404


def test_proposals_execute_as_user_forbidden(client, monkeypatch):
    import routes.api_v1.approvals as apv_mod

    monkeypatch.setattr(
        apv_mod,
        "get_user",
        lambda _req: UserContext(user_id="plain", role="user"),
    )

    resp = client.post("/api/v1/approvals/proposals/1/execute?as_user=alice", json={})
    assert resp.status_code == 403
