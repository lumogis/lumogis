# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for signals/triage_reminders.py (LUM-648).

Wiring + control flow of the snooze scan and morning brief against a fake store
(string-matched SQL + a fake checkout connection for the advisory lock). The real
flip / dedup / enumeration SQL semantics are proven against Postgres in
``tests/integration/test_triage_reminders_live.py``.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
from unittest.mock import MagicMock

import pytest


class _FakeCursor:
    def __init__(self, store):
        self.store = store
        self._last = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, query, params=None):
        q = " ".join(query.split()).lower()
        if "pg_try_advisory_lock" in q:
            self._last = (self.store.lock_ok,)
        elif "pg_advisory_unlock" in q:
            self.store.unlocked = True
            self._last = (True,)
        else:
            self._last = None

    def fetchone(self):
        return self._last


class _FakeConn:
    def __init__(self, store):
        self.store = store
        self.closed = False

    def cursor(self):
        return _FakeCursor(self.store)

    def close(self):
        self.closed = True


class _ReminderStore:
    """Covers the scan UPDATE…RETURNING, brief enumeration, settings, and count."""

    def __init__(self):
        self.lock_ok = True
        self.unlocked = False
        self.worklist: list[dict] = []
        self.brief_users: list[str] = []
        self.settings: dict[str, dict] = {}
        self.counts: dict[str, int] = {}

    @contextmanager
    def checkout_connection(self):
        conn = _FakeConn(self)
        try:
            yield conn
        finally:
            conn.close()

    def fetch_all(self, query, params=None):
        q = " ".join(query.split()).lower()
        # LUM-664: the flip is now UPDATE signal_triage st ... RETURNING copy fields.
        if q.startswith("update signal_triage st"):
            return list(self.worklist)
        if "union" in q and "from signals" in q and "from signal_triage" in q:
            return [{"user_id": u} for u in self.brief_users]
        # LUM-665: batched timezone read for the brief.
        if "from notification_user_settings" in q:
            return [{"user_id": u, "timezone": s.get("timezone")} for u, s in self.settings.items()]
        return []

    def fetch_one(self, query, params=None):
        q = " ".join(query.split()).lower()
        if "count(distinct coalesce" in q:
            return {"n": self.counts.get(params[0], 0)}
        return None


@pytest.fixture
def store(monkeypatch):
    import config as _config

    s = _ReminderStore()
    _config._instances["metadata_store"] = s
    return s


@pytest.fixture
def emit_mock(monkeypatch):
    m = MagicMock()
    from models.notifications import DispatchResult
    from models.notifications import NotificationTier
    from models.notifications import NotificationType

    m.return_value = DispatchResult(
        emit_id="e1",
        user_id="u",
        notification_type=NotificationType.SIGNAL_NEEDS_YOU,
        tier=NotificationTier.ACTION_REQUIRED,
        channels=[],
        outcome="delivered",
    )
    monkeypatch.setattr("services.notifications.dispatcher.emit", m)
    return m


# ─── snooze scan ─────────────────────────────────────────────────────────────


def test_scan_flips_and_emits_single(store, emit_mock):
    # The flip RETURNING now carries the email copy fields (LUM-664) — no extra fetch.
    store.worklist = [
        {
            "user_id": "alice",
            "signal_id": "s1",
            "title": "Rent due",
            "from_display": "Landlord",
            "from_addr": "",
        }
    ]

    from signals import triage_reminders

    triage_reminders._scan_due_snoozes()

    assert emit_mock.call_count == 1
    n = emit_mock.call_args.args[0]
    from models.notifications import NotificationType

    assert n.notification_type == NotificationType.SIGNAL_NEEDS_YOU
    assert n.user_id == "alice"
    assert n.metadata["signal_id"] == "s1"
    assert n.metadata["count"] == 1
    assert n.body == "Rent due"
    assert store.unlocked is True


def test_scan_batches_multiple_per_user(store, emit_mock):
    store.worklist = [
        {"user_id": "alice", "signal_id": "s1", "title": "A", "from_display": "", "from_addr": ""},
        {"user_id": "alice", "signal_id": "s2", "title": "B", "from_display": "", "from_addr": ""},
        {"user_id": "alice", "signal_id": "s3", "title": "C", "from_display": "", "from_addr": ""},
    ]

    from signals import triage_reminders

    triage_reminders._scan_due_snoozes()

    assert emit_mock.call_count == 1
    n = emit_mock.call_args.args[0]
    assert n.metadata["count"] == 3
    assert n.metadata["signal_id"] is None
    assert n.body == "3 snoozed emails are back"


def test_scan_idempotent_empty_worklist_no_emit(store, emit_mock):
    store.worklist = []

    from signals import triage_reminders

    triage_reminders._scan_due_snoozes()
    assert emit_mock.call_count == 0


def test_scan_flips_before_emit_failure_isolated(store, monkeypatch):
    store.worklist = [
        {"user_id": "alice", "signal_id": "s1", "title": "A", "from_display": "", "from_addr": ""},
        {"user_id": "bob", "signal_id": "s2", "title": "B", "from_display": "", "from_addr": ""},
    ]

    calls = []

    def _flaky(notification):
        calls.append(notification.user_id)
        if notification.user_id == "alice":
            raise RuntimeError("boom")
        from models.notifications import DispatchResult
        from models.notifications import NotificationTier
        from models.notifications import NotificationType

        return DispatchResult(
            emit_id="e",
            user_id=notification.user_id,
            notification_type=NotificationType.SIGNAL_NEEDS_YOU,
            tier=NotificationTier.ACTION_REQUIRED,
            channels=[],
            outcome="delivered",
        )

    monkeypatch.setattr("services.notifications.dispatcher.emit", _flaky)

    from signals import triage_reminders

    triage_reminders._scan_due_snoozes()  # must not raise
    assert set(calls) == {"alice", "bob"}  # bob still processed after alice raised


def test_scan_skipped_when_lock_not_acquired(store, emit_mock):
    store.lock_ok = False
    store.worklist = [{"user_id": "alice", "signal_id": "s1"}]

    from signals import triage_reminders

    triage_reminders._scan_due_snoozes()
    emit_mock.assert_not_called()


# ─── morning brief ───────────────────────────────────────────────────────────


def _set_brief_hour_now(monkeypatch, *, match: bool):
    """Point _BRIEF_HOUR at (or away from) the current UTC hour (UTC-tz users)."""
    hour = datetime.now(timezone.utc).hour
    monkeypatch.setattr("signals.triage_reminders._BRIEF_HOUR", hour if match else (hour + 1) % 24)


def test_brief_emits_at_local_hour_when_count_positive(store, emit_mock, monkeypatch):
    _set_brief_hour_now(monkeypatch, match=True)
    store.brief_users = ["alice"]
    store.settings = {"alice": {"timezone": "UTC"}}
    store.counts = {"alice": 2}

    from signals import triage_reminders

    triage_reminders._send_morning_briefs()

    assert emit_mock.call_count == 1
    n = emit_mock.call_args.args[0]
    from models.notifications import NotificationType

    assert n.notification_type == NotificationType.SIGNAL_MORNING_BRIEF
    assert n.metadata["count"] == 2
    assert "2 emails need you today" == n.body


def test_brief_silent_when_zero(store, emit_mock, monkeypatch):
    _set_brief_hour_now(monkeypatch, match=True)
    store.brief_users = ["alice"]
    store.settings = {"alice": {"timezone": "UTC"}}
    store.counts = {"alice": 0}

    from signals import triage_reminders

    triage_reminders._send_morning_briefs()
    emit_mock.assert_not_called()


def test_brief_skips_users_outside_local_hour(store, emit_mock, monkeypatch):
    _set_brief_hour_now(monkeypatch, match=False)
    store.brief_users = ["alice"]
    store.settings = {"alice": {"timezone": "UTC"}}
    store.counts = {"alice": 5}

    from signals import triage_reminders

    triage_reminders._send_morning_briefs()
    emit_mock.assert_not_called()


def test_brief_enumeration_unions_signal_and_triage_owners(store, emit_mock, monkeypatch):
    # A triage-only member (owns no signals) must be enumerated (Fable R3 D1.1).
    _set_brief_hour_now(monkeypatch, match=True)
    store.brief_users = ["owner", "triage_only"]
    store.settings = {"owner": {"timezone": "UTC"}, "triage_only": {"timezone": "UTC"}}
    store.counts = {"owner": 1, "triage_only": 1}

    from signals import triage_reminders

    triage_reminders._send_morning_briefs()

    briefed = {c.args[0].user_id for c in emit_mock.call_args_list}
    assert briefed == {"owner", "triage_only"}
