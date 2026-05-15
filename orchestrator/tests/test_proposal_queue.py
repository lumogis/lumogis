# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for ``services/proposal_queue`` with an in-memory SQL fake."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from actions import proposal_execute
from actions import registry as actions_registry
from actions.executor import ActionResult
from fastapi import HTTPException
from models.actions import ActionSpec
from tests.fakes.fake_proposal_queue_store import FakeProposalQueueStore

import config as _config
from services import proposal_queue


def _as_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@pytest.fixture(autouse=True)
def _proposal_registry_cleanup():
    snapshot = dict(actions_registry._registry)
    yield
    actions_registry._registry.clear()
    actions_registry._registry.update(snapshot)


@pytest.fixture(autouse=True)
def _fake_proposal_store(monkeypatch):
    store = FakeProposalQueueStore()
    monkeypatch.setitem(_config._instances, "metadata_store", store)
    yield store


@pytest.fixture(autouse=True)
def _restore_max_attempts_env(monkeypatch):
    monkeypatch.setenv("ACTION_PROPOSALS_MAX_ATTEMPTS", "3")
    monkeypatch.setattr(proposal_queue, "ACTION_PROPOSALS_MAX_ATTEMPTS", 3)
    yield


def _approved_row(fake: FakeProposalQueueStore, **kwargs):
    pid = kwargs.get("id", fake._next_id)
    fake._next_id = max(fake._next_id, pid + 1)
    now = datetime.now(timezone.utc)
    fake.rows[pid] = {
        "id": pid,
        "user_id": kwargs.get("user_id", "default"),
        "action_name": kwargs.get("action_name", "lumogis.tests.noop_action"),
        "payload": kwargs.get("payload") or {},
        "status": "approved",
        "attempt": kwargs.get("attempt", 0),
        "run_after": kwargs.get("run_after") or now - timedelta(seconds=1),
        "claimed_at": None,
        "claimed_by": None,
        "created_at": kwargs.get("created_at") or now,
        "executed_at": None,
        "finished_at": None,
        "error": None,
    }
    return pid


def test_claim_by_id_success(_fake_proposal_store) -> None:
    pid = _approved_row(_fake_proposal_store, user_id="alice")
    c = proposal_queue.claim_by_id(pid, "alice", "w1")
    assert c is not None
    r = _fake_proposal_store.rows[pid]
    assert r["status"] == "executing"
    assert r["claimed_by"] == "w1"


def test_claim_by_id_loses_race(_fake_proposal_store) -> None:
    pid = _approved_row(_fake_proposal_store, user_id="alice")
    assert proposal_queue.claim_by_id(pid, "alice", "w1") is not None
    assert proposal_queue.claim_by_id(pid, "alice", "w2") is None


def test_claim_by_id_denied_cross_user(_fake_proposal_store) -> None:
    pid = _approved_row(_fake_proposal_store, user_id="alice")
    assert proposal_queue.claim_by_id(pid, "bob", "w1") is None


def test_claim_next_skip_locked(_fake_proposal_store) -> None:
    pid1 = _approved_row(_fake_proposal_store, user_id="u", action_name="x")
    pid2 = _approved_row(_fake_proposal_store, user_id="u", action_name="y")
    assert pid1 != pid2
    a = proposal_queue.claim_next("wa")
    b = proposal_queue.claim_next("wb")
    assert {a.id, b.id} == {pid1, pid2}


def test_reset_stuck_marks_dead(_fake_proposal_store) -> None:
    pid = _approved_row(_fake_proposal_store)
    proposal_queue.claim_by_id(pid, "default", "w-old")
    r = _fake_proposal_store.rows[pid]
    r["claimed_at"] = datetime.now(timezone.utc) - timedelta(seconds=10_000)
    proposal_queue.reset_stuck_claims(stuck_after_seconds=60)
    assert r["status"] == "dead"
    assert r["error"] == proposal_queue.STALE_EXECUTING_ERROR


def test_stuck_after_successful_execute_no_double_side_effect(_fake_proposal_store, monkeypatch):
    monkeypatch.setattr("permissions.check_permission", lambda *a, **k: True)

    n = {"c": 0}

    def _handler(_inp):
        n["c"] += 1
        return ActionResult(success=True, output="ok")

    actions_registry.register_action(
        ActionSpec(
            name="lumogis.tests.proposal_counter_action",
            connector="smtp",
            action_type="draft_email",
            is_write=False,
            is_reversible=False,
            handler=_handler,
        ),
    )
    pid = _approved_row(
        _fake_proposal_store,
        action_name="lumogis.tests.proposal_counter_action",
    )

    def _boom(_p):
        raise RuntimeError("boom")

    monkeypatch.setattr(proposal_queue, "mark_done", _boom)

    with pytest.raises(HTTPException) as ei:
        proposal_execute.claim_and_execute_proposal(pid, worker_id="w-http", user_id="default")
    assert ei.value.status_code == 503

    r = _fake_proposal_store.rows[pid]
    r["claimed_at"] = datetime.now(timezone.utc) - timedelta(days=999)
    proposal_queue.reset_stuck_claims(stuck_after_seconds=60)

    with pytest.raises(HTTPException) as ei2:
        proposal_execute.claim_and_execute_proposal(pid, worker_id="w2-http", user_id="default")
    assert ei2.value.status_code == 400

    assert n["c"] == 1


def test_fail_execution_backoff_uses_run_after(_fake_proposal_store) -> None:
    pid = _approved_row(_fake_proposal_store)
    proposal_queue.claim_by_id(pid, "default", "w")

    proposal_queue.fail_execution(pid, "fail1", max_attempts=3)
    r = _fake_proposal_store.rows[pid]
    assert r["status"] == "approved"
    assert int(r["attempt"]) == 1
    assert _as_utc(r["run_after"]) is not None


def test_fail_execution_dead_when_max_attempts_exceeded(_fake_proposal_store) -> None:
    pid = _approved_row(_fake_proposal_store)
    r = _fake_proposal_store.rows[pid]
    r["attempt"] = 2
    r["claimed_at"] = datetime.now(timezone.utc)
    r["claimed_by"] = "wx"
    r["status"] = "executing"
    proposal_queue.fail_execution(pid, "final", max_attempts=3)
    assert r["status"] == "dead"


def test_claim_next_claimed_row_shape(_fake_proposal_store) -> None:
    _approved_row(_fake_proposal_store)
    claimed = proposal_queue.claim_next("wid")
    assert claimed is not None
    assert isinstance(claimed.attempt, int)
