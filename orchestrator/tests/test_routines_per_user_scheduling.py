# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Per-user APScheduler routine id wiring (plan ``per_user_batch_jobs``)."""

from __future__ import annotations

from collections import namedtuple
from datetime import datetime
from datetime import timezone
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from models.actions import RoutineSpec

_FakeUser = namedtuple("_FakeUser", ["id", "disabled"])


class _FakeJob:
    def __init__(self, job_id: str, sched: MagicMock) -> None:
        self.id = job_id
        self._sched = sched

    def remove(self) -> None:
        self._sched._jobs.pop(self.id, None)


@pytest.fixture
def fake_scheduler(monkeypatch):
    sched = MagicMock()
    sched.running = True
    sched._jobs: dict[str, _FakeJob] = {}

    def add_job(fn, **kwargs):
        jid = kwargs["id"]
        sched._jobs[jid] = _FakeJob(jid, sched)

    def get_job(jid):
        return sched._jobs.get(jid)

    def get_jobs():
        return list(sched._jobs.values())

    sched.add_job.side_effect = add_job
    sched.get_job.side_effect = get_job
    sched.get_jobs.side_effect = get_jobs
    monkeypatch.setattr("config.get_scheduler", lambda: sched)
    return sched


def test_maybe_schedule_uses_per_user_job_id(fake_scheduler, monkeypatch):
    monkeypatch.setattr("config.get_scheduler", lambda: fake_scheduler)
    monkeypatch.setattr("services.routines._routine_jobs", {})

    from services import routines

    spec = RoutineSpec(
        name="weekly_review",
        description="d",
        schedule_cron="0 18 * * 0",
        user_id="alice",
        steps=[{"action_name": "__builtin__weekly_review"}],
        requires_approval=False,
        approved_at=datetime.now(timezone.utc),
        enabled=True,
    )
    routines._maybe_schedule(spec)
    assert "routine_alice_weekly_review" in fake_scheduler._jobs


def test_unschedule_only_removes_target_user_job(fake_scheduler, monkeypatch):
    monkeypatch.setattr("config.get_scheduler", lambda: fake_scheduler)
    monkeypatch.setattr("services.routines._routine_jobs", {})

    from services import routines

    a = RoutineSpec(
        name="weekly_review",
        description="d",
        schedule_cron="0 18 * * 0",
        user_id="alice",
        steps=[{"action_name": "__builtin__weekly_review"}],
        requires_approval=False,
        approved_at=datetime.now(timezone.utc),
        enabled=True,
    )
    b = RoutineSpec(
        name="weekly_review",
        description="d",
        schedule_cron="0 18 * * 0",
        user_id="bob",
        steps=[{"action_name": "__builtin__weekly_review"}],
        requires_approval=False,
        approved_at=datetime.now(timezone.utc),
        enabled=True,
    )
    routines._maybe_schedule(a)
    routines._maybe_schedule(b)
    assert len(fake_scheduler._jobs) == 2
    routines._unschedule("weekly_review", "alice")
    assert "routine_alice_weekly_review" not in fake_scheduler._jobs
    assert "routine_bob_weekly_review" in fake_scheduler._jobs


def test_job_callback_passes_user_id_to_run_routine(monkeypatch):
    from services import routines

    with patch("services.routines.run_routine") as mock_run:
        routines._job_callback("weekly_review", "alice")
    mock_run.assert_called_once_with("weekly_review", user_id="alice")


def test_target_user_ids_returns_enabled_users_when_users_table_populated(monkeypatch):
    from services import routines

    monkeypatch.setattr("services.users.count_users", lambda: 2)
    monkeypatch.setattr(
        "services.users.list_users",
        lambda: [_FakeUser("alice", False), _FakeUser("bob", True)],
    )
    assert routines._target_user_ids() == ["alice"]


def test_target_user_ids_returns_default_when_users_table_empty(monkeypatch):
    from services import routines

    monkeypatch.setattr("services.users.count_users", lambda: 0)
    assert routines._target_user_ids() == ["default"]


def test_ensure_weekly_review_fans_out_to_all_enabled_users(monkeypatch):
    captured: list[RoutineSpec] = []

    def capture(spec: RoutineSpec) -> None:
        captured.append(spec)

    monkeypatch.setattr("services.users.count_users", lambda: 2)
    monkeypatch.setattr(
        "services.users.list_users",
        lambda: [_FakeUser("alice", False), _FakeUser("carol", False)],
    )
    monkeypatch.setattr("services.routines.register_routine", capture)
    from services import routines

    routines._ensure_weekly_review()
    uids = sorted({s.user_id for s in captured if s.name == "weekly_review"})
    assert uids == ["alice", "carol"]
