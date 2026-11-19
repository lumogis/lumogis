# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The system monitor raises the same alert at most once per window (LUM-993).

Before this, every 30-minute check stored a fresh copy of each alert while its
condition held: lumogis-dev had 504 identical "Review queue: 2 item(s) awaiting
resolution" signals after 11 days, and Email signals listed them.
"""

import pytest
from signals import system_monitor

import config


class _FakeStore:
    def __init__(self, recent_titles=(), fail=False):
        self.recent_titles = set(recent_titles)
        self.fail = fail
        self.queries = []

    def fetch_one(self, query, params=None):
        self.queries.append((query, params))
        if self.fail:
            raise RuntimeError("db down")
        title = params[0]
        return {"hit": 1} if title in self.recent_titles else None


ALERT = {"title": "Review queue: 2 item(s) awaiting resolution", "body": "b", "importance": 0.5}


@pytest.fixture
def run(monkeypatch):
    sent = []
    monkeypatch.setattr(system_monitor, "_check_disk", lambda: [])
    monkeypatch.setattr(system_monitor, "_check_backup_age", lambda: [])
    monkeypatch.setattr(system_monitor, "_check_inbox_depth", lambda: [])
    monkeypatch.setattr(system_monitor, "_check_review_queue", lambda: [dict(ALERT)])
    monkeypatch.setattr(
        system_monitor, "process_signal", lambda sig, user_id: sent.append(sig.title)
    )

    def _run(store):
        monkeypatch.setattr(config, "get_metadata_store", lambda: store)
        system_monitor._run_checks()
        return sent

    return _run


def test_new_alert_is_raised(run):
    assert run(_FakeStore()) == [ALERT["title"]]


def test_alert_raised_within_window_is_skipped(run):
    store = _FakeStore(recent_titles={ALERT["title"]})
    assert run(store) == []
    query, params = store.queries[0]
    assert "source_id = '__system__'" in query and params == (ALERT["title"], 24 * 3600.0)


def test_changed_value_is_a_new_alert(run):
    # The title carries the count, so a growing queue alerts again at once.
    earlier = "Review queue: 1 item(s) awaiting resolution"
    assert run(_FakeStore(recent_titles={earlier})) == [ALERT["title"]]


def test_lookup_failure_still_raises_the_alert(run):
    assert run(_FakeStore(fail=True)) == [ALERT["title"]]


def test_zero_window_turns_the_check_off(run, monkeypatch):
    monkeypatch.setattr(system_monitor, "_REPEAT_HOURS", 0)
    store = _FakeStore(recent_titles={ALERT["title"]})
    assert run(store) == [ALERT["title"]]
    assert store.queries == []
