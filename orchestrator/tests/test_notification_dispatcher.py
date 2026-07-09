# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Dispatcher decision sequence tests."""

from __future__ import annotations

from datetime import datetime
from datetime import time

import pytest
from models.notifications import ChannelDeliveryResult
from models.notifications import ChannelId
from models.notifications import NotificationTier
from models.notifications import NotificationTierPolicyRow
from models.notifications import NotificationType
from models.notifications import TypedNotification
from services.notifications.dispatcher import emit
from services.notifications.dispatcher import is_in_quiet_hours
from services.notifications.dispatcher import resolve_effective_channels

import config


class _StubChannel:
    def __init__(self, channel_id: ChannelId, result: ChannelDeliveryResult):
        self.channel_id = channel_id
        self._result = result
        self.calls = 0

    def deliver(self, notification: TypedNotification) -> ChannelDeliveryResult:
        self.calls += 1
        return self._result


@pytest.fixture(autouse=True)
def _reset_notif(monkeypatch):
    config.reset_notification_factories()
    config.invalidate_tier_policy_cache()
    yield
    config.reset_notification_factories()
    config.invalidate_tier_policy_cache()


def _tier_policies():
    return {
        NotificationTier.URGENT: NotificationTierPolicyRow(
            tier=NotificationTier.URGENT,
            bypass_quiet_hours=True,
            default_channels=[ChannelId.NTFY, ChannelId.WEB_PUSH, ChannelId.IN_APP],
        ),
        NotificationTier.INFORMATIONAL: NotificationTierPolicyRow(
            tier=NotificationTier.INFORMATIONAL,
            bypass_quiet_hours=False,
            default_channels=[ChannelId.NTFY, ChannelId.WEB_PUSH, ChannelId.IN_APP],
        ),
        NotificationTier.BACKGROUND: NotificationTierPolicyRow(
            tier=NotificationTier.BACKGROUND,
            bypass_quiet_hours=False,
            default_channels=[ChannelId.IN_APP],
        ),
        NotificationTier.ACTION_REQUIRED: NotificationTierPolicyRow(
            tier=NotificationTier.ACTION_REQUIRED,
            bypass_quiet_hours=False,
            default_channels=[ChannelId.NTFY, ChannelId.WEB_PUSH, ChannelId.IN_APP],
        ),
    }


def _patch_prefs(monkeypatch, *, sparse=None, settings=None):
    sparse = sparse or {}
    settings = settings or {}
    monkeypatch.setattr(
        "services.notifications.dispatcher._load_sparse_prefs",
        lambda _uid: sparse,
    )
    monkeypatch.setattr(
        "services.notifications.dispatcher._load_user_settings",
        lambda _uid: settings,
    )
    monkeypatch.setattr(
        "services.notifications.dispatcher.load_tier_policies",
        _tier_policies,
    )
    monkeypatch.setattr(
        "services.notifications.preferences.load_tier_policies",
        _tier_policies,
    )


def test_emit_assigns_emit_id_when_missing(monkeypatch):
    _patch_prefs(monkeypatch)
    ntfy = _StubChannel(
        ChannelId.NTFY, ChannelDeliveryResult(channel=ChannelId.NTFY, status="delivered")
    )
    monkeypatch.setitem(config._instances, "notification_channels", {ChannelId.NTFY: ntfy})

    result = emit(
        TypedNotification(
            user_id="u1",
            notification_type=NotificationType.SIGNAL_RECEIVED,
            title="t",
            body="b",
        )
    )
    assert result.emit_id
    assert len(result.emit_id) >= 8


def test_emit_rejects_empty_user_id(monkeypatch):
    _patch_prefs(monkeypatch)
    result = emit(
        TypedNotification(
            user_id="",
            notification_type=NotificationType.SIGNAL_RECEIVED,
            title="t",
            body="b",
        )
    )
    assert result.outcome == "failed"
    assert result.channels == []


def test_tier_caps_channels_background_in_app_only(monkeypatch):
    _patch_prefs(monkeypatch)
    channels = resolve_effective_channels(
        "u1",
        NotificationType.CONSOLIDATION_DONE,
        NotificationTier.BACKGROUND,
        tier_policies=_tier_policies(),
    )
    assert channels == [ChannelId.IN_APP]


def test_sparse_disable_skips_channel(monkeypatch):
    _patch_prefs(
        monkeypatch,
        sparse={(NotificationType.SIGNAL_RECEIVED, ChannelId.NTFY): False},
    )
    ntfy = _StubChannel(
        ChannelId.NTFY, ChannelDeliveryResult(channel=ChannelId.NTFY, status="delivered")
    )
    in_app = _StubChannel(
        ChannelId.IN_APP, ChannelDeliveryResult(channel=ChannelId.IN_APP, status="delivered")
    )
    monkeypatch.setitem(
        config._instances,
        "notification_channels",
        {ChannelId.NTFY: ntfy, ChannelId.IN_APP: in_app, ChannelId.WEB_PUSH: in_app},
    )

    result = emit(
        TypedNotification(
            user_id="u1",
            notification_type=NotificationType.SIGNAL_RECEIVED,
            title="t",
            body="b",
        )
    )
    assert ntfy.calls == 0
    assert any(r.channel == ChannelId.NTFY and r.status == "skipped" for r in result.channels)


def test_quiet_hours_skips_push_not_in_app(monkeypatch):
    _patch_prefs(monkeypatch)
    monkeypatch.setattr(
        "services.notifications.dispatcher.is_in_quiet_hours",
        lambda *_a, **_k: True,
    )

    ntfy = _StubChannel(
        ChannelId.NTFY, ChannelDeliveryResult(channel=ChannelId.NTFY, status="delivered")
    )
    in_app = _StubChannel(
        ChannelId.IN_APP, ChannelDeliveryResult(channel=ChannelId.IN_APP, status="delivered")
    )
    monkeypatch.setitem(
        config._instances,
        "notification_channels",
        {ChannelId.NTFY: ntfy, ChannelId.IN_APP: in_app, ChannelId.WEB_PUSH: ntfy},
    )

    result = emit(
        TypedNotification(
            user_id="u1",
            notification_type=NotificationType.SIGNAL_RECEIVED,
            title="t",
            body="b",
        )
    )
    assert ntfy.calls == 0
    assert in_app.calls == 1
    assert any(r.reason == "quiet_hours" for r in result.channels)


def test_urgent_bypass_delivers_push_and_audits(monkeypatch):
    _patch_prefs(monkeypatch)
    monkeypatch.setattr(
        "services.notifications.dispatcher.is_in_quiet_hours",
        lambda *_a, **_k: True,
    )

    ntfy = _StubChannel(
        ChannelId.NTFY, ChannelDeliveryResult(channel=ChannelId.NTFY, status="delivered")
    )
    monkeypatch.setitem(
        config._instances,
        "notification_channels",
        {ChannelId.NTFY: ntfy, ChannelId.IN_APP: ntfy, ChannelId.WEB_PUSH: ntfy},
    )

    emit(
        TypedNotification(
            user_id="u1",
            notification_type=NotificationType.SECURITY_ALERT,
            title="t",
            body="b",
        )
    )
    assert ntfy.calls >= 1


def test_channel_failure_does_not_abort_others(monkeypatch):
    _patch_prefs(monkeypatch)
    ntfy = _StubChannel(
        ChannelId.NTFY, ChannelDeliveryResult(channel=ChannelId.NTFY, status="failed", reason="x")
    )
    in_app = _StubChannel(
        ChannelId.IN_APP, ChannelDeliveryResult(channel=ChannelId.IN_APP, status="delivered")
    )
    monkeypatch.setitem(
        config._instances,
        "notification_channels",
        {ChannelId.NTFY: ntfy, ChannelId.IN_APP: in_app, ChannelId.WEB_PUSH: ntfy},
    )

    result = emit(
        TypedNotification(
            user_id="u1",
            notification_type=NotificationType.SIGNAL_RECEIVED,
            title="t",
            body="b",
        )
    )
    assert in_app.calls == 1
    assert result.outcome == "partial"


def test_ntfy_channel_skips_when_backend_none(monkeypatch):
    monkeypatch.setenv("NOTIFIER_BACKEND", "none")
    from services.notifications.channels.ntfy_channel import NtfyChannel

    result = NtfyChannel().deliver(
        TypedNotification(
            user_id="u1",
            notification_type=NotificationType.SIGNAL_RECEIVED,
            title="t",
            body="b",
        )
    )
    assert result.status == "skipped"
    assert result.reason == "deployment_unavailable"


def test_web_push_channel_skips_unmapped_type(monkeypatch):
    from services.notifications.channels.web_push_channel import WebPushChannel

    result = WebPushChannel().deliver(
        TypedNotification(
            user_id="u1",
            notification_type=NotificationType.SIGNAL_RECEIVED,
            title="t",
            body="b",
        )
    )
    assert result.status == "skipped"
    assert result.reason == "template_not_mapped"


def test_is_in_quiet_hours_overnight():
    start = time(22, 0)
    end = time(7, 0)
    assert is_in_quiet_hours(datetime(2026, 1, 1, 23, 0), start, end)
    assert not is_in_quiet_hours(datetime(2026, 1, 1, 12, 0), start, end)


def test_urgent_zero_push_still_delivers_in_app(monkeypatch):
    _patch_prefs(
        monkeypatch,
        sparse={
            (NotificationType.SECURITY_ALERT, ChannelId.NTFY): False,
            (NotificationType.SECURITY_ALERT, ChannelId.WEB_PUSH): False,
        },
    )
    ntfy = _StubChannel(
        ChannelId.NTFY, ChannelDeliveryResult(channel=ChannelId.NTFY, status="delivered")
    )
    web_push = _StubChannel(
        ChannelId.WEB_PUSH,
        ChannelDeliveryResult(channel=ChannelId.WEB_PUSH, status="delivered"),
    )
    in_app = _StubChannel(
        ChannelId.IN_APP,
        ChannelDeliveryResult(channel=ChannelId.IN_APP, status="delivered"),
    )
    monkeypatch.setitem(
        config._instances,
        "notification_channels",
        {ChannelId.NTFY: ntfy, ChannelId.WEB_PUSH: web_push, ChannelId.IN_APP: in_app},
    )

    result = emit(
        TypedNotification(
            user_id="u1",
            notification_type=NotificationType.SECURITY_ALERT,
            title="t",
            body="b",
        )
    )

    assert ntfy.calls == 0
    assert web_push.calls == 0
    assert in_app.calls == 1
    assert any(r.channel == ChannelId.IN_APP and r.status == "delivered" for r in result.channels)
    assert result.tier == NotificationTier.URGENT
