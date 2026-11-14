# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-648 — signal_processor._notify email rescope.

The load-bearing behaviour (Fable D6.1): a needs-you email fires the single
SIGNAL_NEEDS_YOU push and sets ``notified=True``; a low-importance email is
in-app-only (BACKGROUND) and sets ``notified=False`` so ``deriveLane``
(``notified ∨ importance≥0.6``) does NOT re-inflate Needs-you. Non-email signals
are unchanged.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from unittest.mock import MagicMock

import pytest
from models.signals import Signal

from services import signal_processor as sp


def _signal(**kw) -> Signal:
    base = dict(
        signal_id="sig1",
        source_id="src-abc",
        title="Rent due",
        url="imap://src-abc/100/5",
        published_at=None,
        content_summary="pay the rent",
        raw_content="",
        entities=[],
        topics=[],
        importance_score=0.9,
        relevance_score=0.8,
        notified=False,
        created_at=datetime.now(timezone.utc),
        user_id="u1",
        source_type="imap",
        category="",
        from_addr="landlord@x.com",
        from_display="Landlord",
    )
    base.update(kw)
    return Signal(**base)


@pytest.fixture
def emit_mock(monkeypatch):
    m = MagicMock()
    from models.notifications import DispatchResult
    from models.notifications import NotificationTier
    from models.notifications import NotificationType

    m.return_value = DispatchResult(
        emit_id="e",
        user_id="u1",
        notification_type=NotificationType.SIGNAL_RECEIVED,
        tier=NotificationTier.INFORMATIONAL,
        channels=[],
        outcome="delivered",
    )
    monkeypatch.setattr("services.notifications.dispatcher.emit", m)
    return m


def test_needs_you_email_emits_needs_you_type_and_sets_notified_true(emit_mock):
    from models.notifications import NotificationType

    result = sp._notify(_signal(importance_score=0.9))
    assert result is True
    n = emit_mock.call_args.args[0]
    assert n.notification_type == NotificationType.SIGNAL_NEEDS_YOU
    assert "Landlord" in n.title
    assert n.body == "Rent due"
    assert n.metadata["target_path"] == "/email"


def test_low_importance_email_in_app_only_and_notified_false(emit_mock):
    from models.notifications import NotificationTier
    from models.notifications import NotificationType

    result = sp._notify(_signal(importance_score=0.2))
    # notified=False regardless of the BACKGROUND emit succeeding in-app — this is
    # the assertion that proves the rescope actually rescopes (the old
    # channel-only test passed while notified=True re-inflated the lane).
    assert result is False
    n = emit_mock.call_args.args[0]
    assert n.notification_type == NotificationType.SIGNAL_RECEIVED
    assert n.tier == NotificationTier.BACKGROUND


def test_non_email_signal_unchanged(emit_mock):
    from models.notifications import NotificationType

    rss = _signal(source_type="", url="https://news/x", importance_score=0.9)
    result = sp._notify(rss)
    # Non-email keeps the generic SIGNAL_RECEIVED path with default tier (None →
    # INFORMATIONAL) and delivery-derived notified.
    assert result is True
    n = emit_mock.call_args.args[0]
    assert n.notification_type == NotificationType.SIGNAL_RECEIVED
    assert n.tier is None


def test_notify_swallows_errors_returns_false(monkeypatch):
    def _boom(_n):
        raise RuntimeError("dispatch down")

    monkeypatch.setattr("services.notifications.dispatcher.emit", _boom)
    assert sp._notify(_signal()) is False
