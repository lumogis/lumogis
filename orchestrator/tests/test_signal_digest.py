# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for signals/digest.py per-user fanout.

Pins the ADR 018 ntfy-migration behavior change: the digest enumerates
distinct ``user_id`` values that produced signals in the window and
emits one notification per user (previous behavior was a single
household-global notification).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class _DigestStore:
    """Toy metadata store covering only the two SELECTs the digest issues."""

    def __init__(self, signals_by_user: dict[str, list[dict]]):
        self.signals_by_user = signals_by_user

    def fetch_all(self, query: str, params: tuple | None = None):
        q = " ".join(query.split()).lower()
        if q.startswith("select distinct user_id from signals"):
            return [{"user_id": uid} for uid in sorted(self.signals_by_user)]
        if q.startswith("select title, url, content_summary"):
            since, user_id, limit = params
            rows = list(self.signals_by_user.get(user_id, []))[:limit]
            return rows
        return []

    def fetch_one(self, query: str, params: tuple | None = None):
        return None

    def execute(self, query: str, params: tuple | None = None):
        return None


@pytest.fixture
def install_store(monkeypatch):
    def _install(signals_by_user):
        import config as _config
        store = _DigestStore(signals_by_user)
        _config._instances["metadata_store"] = store
        return store
    return _install


def test_send_digest_fans_out_per_user(install_store, monkeypatch):
    install_store({
        "alice": [
            {"title": "A1", "url": "https://e/a1", "content_summary": "s",
             "relevance_score": 0.5, "importance_score": 0.5},
        ],
        "bob": [
            {"title": "B1", "url": "https://e/b1", "content_summary": "s",
             "relevance_score": 0.7, "importance_score": 0.7},
            {"title": "B2", "url": "https://e/b2", "content_summary": "s",
             "relevance_score": 0.6, "importance_score": 0.6},
        ],
    })

    notifier = MagicMock()
    notifier.notify.return_value = True
    import config as _config
    monkeypatch.setattr(_config, "get_notifier", lambda: notifier)

    from signals import digest
    digest._send_digest()

    assert notifier.notify.call_count == 2
    user_ids = {call.kwargs["user_id"] for call in notifier.notify.call_args_list}
    assert user_ids == {"alice", "bob"}


def test_send_digest_no_signals_skips(install_store, monkeypatch):
    install_store({})

    notifier = MagicMock()
    import config as _config
    monkeypatch.setattr(_config, "get_notifier", lambda: notifier)

    from signals import digest
    digest._send_digest()
    assert notifier.notify.call_count == 0


def test_send_digest_continues_after_one_user_error(install_store, monkeypatch):
    install_store({
        "alice": [{"title": "A1", "url": "u", "content_summary": "s",
                   "relevance_score": 0.5, "importance_score": 0.5}],
        "bob": [{"title": "B1", "url": "u", "content_summary": "s",
                 "relevance_score": 0.5, "importance_score": 0.5}],
    })

    notifier = MagicMock()

    def _flaky(title, message, priority, *, user_id):
        if user_id == "alice":
            raise RuntimeError("boom")
        return True

    notifier.notify.side_effect = _flaky
    import config as _config
    monkeypatch.setattr(_config, "get_notifier", lambda: notifier)

    from signals import digest
    digest._send_digest()
    assert notifier.notify.call_count == 2
