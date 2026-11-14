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


# ---------------------------------------------------------------------------
# LUM-762 — failure is terminal on the first attempt.
#
# `fail_execution` and its `executing -> approved` edge are gone: nothing drained
# the queue, so a "retry with backoff" only stranded rows (and pinned their staged
# attachments, since `approved` is a LIVE state for the attachment sweep). These
# pin that ALL failure kinds converge on one disposal.
#
# NOTE ON PROOF: these run against `FakeProposalQueueStore`, which pattern-matches
# the SQL string and re-implements the logic in Python. They prove the DISPOSAL
# WIRING in `proposal_execute`, not the SQL. The statements themselves are proven
# in `test_proposal_queue_migration.py` against real Postgres.
# ---------------------------------------------------------------------------


def _register_result_action(name: str, result: ActionResult) -> None:
    actions_registry.register_action(
        ActionSpec(
            name=name,
            connector="smtp",
            action_type="draft_email",
            is_write=False,
            is_reversible=False,
            handler=lambda _inp: result,
        ),
    )


def test_retry_ladder_is_gone(_fake_proposal_store) -> None:
    """The symbols, not just the behaviour. A partial revert re-adds these first."""
    assert not hasattr(proposal_queue, "fail_execution")
    assert not hasattr(proposal_queue, "claim_next")
    assert not hasattr(proposal_queue, "CLAIM_NEXT_SQL")
    assert not hasattr(proposal_queue, "ACTION_PROPOSALS_MAX_ATTEMPTS")


def test_non_permanent_failure_dead_letters(_fake_proposal_store, monkeypatch) -> None:
    monkeypatch.setattr("permissions.check_permission", lambda *a, **k: True)
    _register_result_action(
        "lumogis.tests.soft_fail",
        ActionResult(success=False, output="", error="transient boom", permanent=False),
    )
    pid = _approved_row(_fake_proposal_store, action_name="lumogis.tests.soft_fail")

    res = proposal_execute.claim_and_execute_proposal(pid, worker_id="w", user_id="default")

    assert res.success is False and res.permanent is False
    r = _fake_proposal_store.rows[pid]
    assert r["status"] == "dead", "a non-permanent failure must NOT return to 'approved'"
    assert r["finished_at"] is not None
    assert int(r["attempt"]) == 1
    assert r["claimed_at"] is None and r["claimed_by"] is None


def test_permanent_failure_still_dead_letters(_fake_proposal_store, monkeypatch) -> None:
    """Unchanged behaviour, pinned so the two branches are proven to CONVERGE."""
    monkeypatch.setattr("permissions.check_permission", lambda *a, **k: True)
    _register_result_action(
        "lumogis.tests.hard_fail",
        ActionResult(success=False, output="", error="stale", permanent=True, code="stale"),
    )
    pid = _approved_row(_fake_proposal_store, action_name="lumogis.tests.hard_fail")

    res = proposal_execute.claim_and_execute_proposal(pid, worker_id="w", user_id="default")

    assert res.success is False and res.permanent is True
    assert _fake_proposal_store.rows[pid]["status"] == "dead"


def test_unknown_action_dead_letters(_fake_proposal_store, monkeypatch) -> None:
    """The reachable zombie neither LUM-762 nor its exploration named.

    `executor.execute` returns `success=False, permanent=False` for an unregistered
    action BEFORE any permission check, and `proposal_execute`'s pre-claim 403 is
    guarded by `if spec is not None` so it does not catch it. Before LUM-762 such a
    row went back to `approved` forever.
    """
    monkeypatch.setattr("permissions.check_permission", lambda *a, **k: True)
    pid = _approved_row(_fake_proposal_store, action_name="lumogis.tests.never_registered")

    res = proposal_execute.claim_and_execute_proposal(pid, worker_id="w", user_id="default")

    assert res.success is False
    r = _fake_proposal_store.rows[pid]
    assert r["status"] == "dead"
    # The error string is carried through verbatim on purpose: this is the one
    # `dead` population that is retryable in spirit, so LUM-793's failed-send
    # listing needs to tell it apart. (It briefly had a second cause — handlers
    # registered inside main.py's BATCH_QUEUE_ENABLED gate, so disabling the batch
    # queue emptied the registry. Fixed in LUM-794; this now means a genuinely
    # absent action.)
    assert "Unknown action" in (r["error"] or "")


def test_successful_execute_still_marks_done(_fake_proposal_store, monkeypatch) -> None:
    """The happy path is the one thing that must not move.

    Without this, a refactor that dead-lettered EVERYTHING would pass the three
    failure tests above.
    """
    monkeypatch.setattr("permissions.check_permission", lambda *a, **k: True)
    _register_result_action("lumogis.tests.ok_action", ActionResult(success=True, output="sent"))
    pid = _approved_row(_fake_proposal_store, action_name="lumogis.tests.ok_action")

    res = proposal_execute.claim_and_execute_proposal(pid, worker_id="w", user_id="default")

    assert res.success is True
    r = _fake_proposal_store.rows[pid]
    assert r["status"] == "done"
    assert r["executed_at"] is not None and r["finished_at"] is not None


def test_dead_row_cannot_be_reclaimed(_fake_proposal_store) -> None:
    pid = _approved_row(_fake_proposal_store)
    _fake_proposal_store.rows[pid]["status"] = "dead"
    assert proposal_queue.claim_by_id(pid, "default", "w") is None


def test_claim_by_id_rejects_future_run_after(_fake_proposal_store) -> None:
    """Fake-layer mirror ONLY — this does NOT prove the SQL.

    `FakeProposalQueueStore` re-implements the predicate in Python, so this asserts
    the fake tracks production. `test_claim_by_id_sql_refuses_future_run_after` in
    the migration lane is the real proof (mutation B).
    """
    pid = _approved_row(
        _fake_proposal_store,
        run_after=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    assert proposal_queue.claim_by_id(pid, "default", "w") is None


# ---------------------------------------------------------------------------
# LUM-762 P2 — a deferred row is a 409, not a 400 naming 'approved'.
# ---------------------------------------------------------------------------


def test_deferred_row_returns_409_not_400(_fake_proposal_store, monkeypatch) -> None:
    monkeypatch.setattr("permissions.check_permission", lambda *a, **k: True)
    deferred_until = datetime.now(timezone.utc) + timedelta(minutes=5)
    pid = _approved_row(_fake_proposal_store, run_after=deferred_until)

    with pytest.raises(HTTPException) as ei:
        proposal_execute.claim_and_execute_proposal(pid, worker_id="w", user_id="default")

    assert ei.value.status_code == 409, "a legal state + timing conflict is 409, not 400"
    assert ei.value.detail["error"] == "proposal_deferred"
    assert ei.value.detail["proposal_id"] == pid
    assert ei.value.detail["retry_after"] == deferred_until.isoformat(), (
        "retry_after must be the row's own run_after, not a recomputed value"
    )
    assert _fake_proposal_store.rows[pid]["status"] == "approved", "must not be consumed"


def test_ready_row_is_not_treated_as_deferred(_fake_proposal_store, monkeypatch) -> None:
    """Guards the way the deferred branch would most plausibly go wrong: by
    swallowing the happy path."""
    monkeypatch.setattr("permissions.check_permission", lambda *a, **k: True)
    _register_result_action("lumogis.tests.ready_action", ActionResult(success=True, output="ok"))
    pid = _approved_row(
        _fake_proposal_store,
        action_name="lumogis.tests.ready_action",
        run_after=datetime.now(timezone.utc) - timedelta(minutes=5),
    )

    res = proposal_execute.claim_and_execute_proposal(pid, worker_id="w", user_id="default")

    assert res.success is True
    assert _fake_proposal_store.rows[pid]["status"] == "done"


# ---------------------------------------------------------------------------
# LUM-762 — the sweeper flag says what it gates.
#
# `ACTION_PROPOSALS_QUEUE_ENABLED` gated a SWEEPER and never gated a queue, which
# is part of why this module read as a live queue for three releases. Tested as a
# pure resolver rather than through APScheduler registration: NO test in this tree
# exercises lifespan job registration for any queue (not batch_queue, not the purge
# sweeper), so building that harness for an env rename would be disproportionate —
# and the precedence is the part that can actually be wrong.
# ---------------------------------------------------------------------------


def test_sweeper_flag_prefers_the_new_name() -> None:
    from main import _proposal_sweeper_enabled

    env = {"ACTION_PROPOSALS_SWEEPER_ENABLED": "false", "ACTION_PROPOSALS_QUEUE_ENABLED": "true"}
    assert _proposal_sweeper_enabled(env) is False


def test_sweeper_flag_honours_the_legacy_name(caplog) -> None:
    from main import _proposal_sweeper_enabled

    with caplog.at_level("WARNING"):
        assert _proposal_sweeper_enabled({"ACTION_PROPOSALS_QUEUE_ENABLED": "false"}) is False
    assert any("deprecated" in r.message for r in caplog.records), "must warn, not silently accept"


def test_sweeper_flag_defaults_on_and_parses_falsey() -> None:
    from main import _proposal_sweeper_enabled

    assert _proposal_sweeper_enabled({}) is True
    for falsey in ("false", "FALSE", "0", "no"):
        assert _proposal_sweeper_enabled({"ACTION_PROPOSALS_SWEEPER_ENABLED": falsey}) is False
    assert _proposal_sweeper_enabled({"ACTION_PROPOSALS_SWEEPER_ENABLED": "true"}) is True


def test_no_module_still_calls_the_retry_ladder() -> None:
    """No EXECUTABLE reference to the deleted ladder survives.

    Greps module SOURCE rather than `__doc__`, because `dead_letter`'s own FUNCTION
    docstring named `fail_execution` and `errors.py` described the backoff in prose —
    neither visible to a module-`__doc__` assertion.

    Deliberately targets **calls and parameters**, not the words. Prose explaining
    why the ladder was removed is worth keeping, and the first version of this test
    banned the word outright — which would have forced the removal of exactly the
    history that stops someone rebuilding it. Asserts on a DELETED symbol, so it
    cannot pass vacuously.
    """
    import inspect
    import re

    from actions import errors as actions_errors

    banned = re.compile(r"fail_execution\s*\(|def\s+fail_execution|max_attempts\s*[=:]")
    for mod in (proposal_queue, proposal_execute, actions_errors):
        hits = [
            line.strip()
            for line in inspect.getsource(mod).splitlines()
            if banned.search(line) and not line.strip().startswith("#")
        ]
        assert not hits, f"{mod.__name__} still references the retry ladder in code: {hits}"
