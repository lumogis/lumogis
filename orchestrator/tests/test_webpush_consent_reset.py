# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-648 — one-time notify_on_signals reset for fresh consent (Sonnet R2)."""

from __future__ import annotations

import pytest


class _FlagStore:
    def __init__(self):
        self.flags: dict[str, str] = {}
        self.updates: list[str] = []

    def fetch_one(self, query, params=None):
        q = " ".join(query.split()).lower()
        if "from app_settings where key" in q:
            key = params[0]
            return {"value": self.flags[key]} if key in self.flags else None
        return None

    def execute(self, query, params=None):
        q = " ".join(query.split()).lower()
        if q.startswith("update webpush_subscriptions set notify_on_signals = false"):
            self.updates.append("reset")
        elif q.startswith("insert into app_settings"):
            self.flags[params[0]] = params[1]


@pytest.fixture
def store(monkeypatch):
    import config as _config

    s = _FlagStore()
    _config._instances["metadata_store"] = s
    return s


def test_reset_runs_once_then_marks_flag(store):
    from services.notifications import migrate_webpush_prefs as m

    m.reset_notify_on_signals_for_consent()
    assert store.updates == ["reset"]
    assert m._NOTIFY_ON_SIGNALS_RESET_FLAG_KEY in store.flags


def test_reset_is_idempotent_on_second_run(store):
    from services.notifications import migrate_webpush_prefs as m

    m.reset_notify_on_signals_for_consent()
    m.reset_notify_on_signals_for_consent()
    # The UPDATE fired exactly once; the flag guards the second run.
    assert store.updates == ["reset"]


def test_reset_is_independent_of_seeder_flag(store):
    from services.notifications import migrate_webpush_prefs as m

    # The older seeder flag being set must NOT suppress the consent reset.
    store.flags[m._SEEDER_FLAG_KEY] = "1"
    m.reset_notify_on_signals_for_consent()
    assert store.updates == ["reset"]
