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
        self.advisory_try_ok: bool = True

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
        q = " ".join(query.split()).lower()
        if "pg_try_advisory_lock" in q:
            return {"ok": self.advisory_try_ok}
        if "pg_advisory_unlock" in q:
            return {"ok": True}
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
    install_store(
        {
            "alice": [
                {
                    "title": "A1",
                    "url": "https://e/a1",
                    "content_summary": "s",
                    "relevance_score": 0.5,
                    "importance_score": 0.5,
                },
            ],
            "bob": [
                {
                    "title": "B1",
                    "url": "https://e/b1",
                    "content_summary": "s",
                    "relevance_score": 0.7,
                    "importance_score": 0.7,
                },
                {
                    "title": "B2",
                    "url": "https://e/b2",
                    "content_summary": "s",
                    "relevance_score": 0.6,
                    "importance_score": 0.6,
                },
            ],
        }
    )

    emit_mock = MagicMock()
    from models.notifications import DispatchResult
    from models.notifications import NotificationTier
    from models.notifications import NotificationType

    emit_mock.return_value = DispatchResult(
        emit_id="e1",
        user_id="alice",
        notification_type=NotificationType.SIGNAL_DIGEST,
        tier=NotificationTier.INFORMATIONAL,
        channels=[],
        outcome="delivered",
    )
    monkeypatch.setattr("services.notifications.dispatcher.emit", emit_mock)

    from signals import digest

    digest._send_digest()

    assert emit_mock.call_count == 2
    user_ids = {call.args[0].user_id for call in emit_mock.call_args_list}
    assert user_ids == {"alice", "bob"}


def test_send_digest_no_signals_skips(install_store, monkeypatch):
    install_store({})

    emit_mock = MagicMock()
    monkeypatch.setattr("services.notifications.dispatcher.emit", emit_mock)

    from signals import digest

    digest._send_digest()
    assert emit_mock.call_count == 0


def test_send_digest_continues_after_one_user_error(install_store, monkeypatch):
    install_store(
        {
            "alice": [
                {
                    "title": "A1",
                    "url": "u",
                    "content_summary": "s",
                    "relevance_score": 0.5,
                    "importance_score": 0.5,
                }
            ],
            "bob": [
                {
                    "title": "B1",
                    "url": "u",
                    "content_summary": "s",
                    "relevance_score": 0.5,
                    "importance_score": 0.5,
                }
            ],
        }
    )

    emit_mock = MagicMock()

    def _flaky(notification):
        if notification.user_id == "alice":
            raise RuntimeError("boom")
        from models.notifications import DispatchResult
        from models.notifications import NotificationTier
        from models.notifications import NotificationType

        return DispatchResult(
            emit_id="e1",
            user_id=notification.user_id,
            notification_type=NotificationType.SIGNAL_DIGEST,
            tier=NotificationTier.INFORMATIONAL,
            channels=[],
            outcome="delivered",
        )

    emit_mock.side_effect = _flaky
    monkeypatch.setattr("services.notifications.dispatcher.emit", emit_mock)

    from signals import digest

    digest._send_digest()
    assert emit_mock.call_count == 2


def test_digest_skips_when_lock_not_acquired(install_store, monkeypatch):
    store = install_store(
        {
            "alice": [
                {
                    "title": "A1",
                    "url": "u",
                    "content_summary": "s",
                    "relevance_score": 0.5,
                    "importance_score": 0.5,
                }
            ],
        }
    )
    store.advisory_try_ok = False

    emit_mock = MagicMock()
    monkeypatch.setattr("services.notifications.dispatcher.emit", emit_mock)

    from signals import digest

    digest._send_digest()

    emit_mock.assert_not_called()


def test_digest_runs_when_lock_acquired(install_store, monkeypatch):
    install_store(
        {
            "zoe": [
                {
                    "title": "Z1",
                    "url": "u",
                    "content_summary": "s",
                    "relevance_score": 0.5,
                    "importance_score": 0.5,
                }
            ],
        }
    )

    emit_mock = MagicMock()
    from models.notifications import DispatchResult
    from models.notifications import NotificationTier
    from models.notifications import NotificationType

    emit_mock.return_value = DispatchResult(
        emit_id="e1",
        user_id="zoe",
        notification_type=NotificationType.SIGNAL_DIGEST,
        tier=NotificationTier.INFORMATIONAL,
        channels=[],
        outcome="delivered",
    )
    monkeypatch.setattr("services.notifications.dispatcher.emit", emit_mock)

    from signals import digest

    digest._send_digest()

    assert emit_mock.call_count == 1
    assert emit_mock.call_args.args[0].user_id == "zoe"
