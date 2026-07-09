# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""SSE event-name invariant for routine elevation."""

import json
from unittest.mock import patch

import pytest
from models.notifications import NotificationTier
from models.notifications import NotificationType
from models.notifications import TypedNotification
from services.notifications.channels.in_app_channel import InAppChannel
from services.notifications.channels.in_app_channel import _sse_payload

_FORBIDDEN_SSE_KEYS = frozenset(
    {
        "token",
        "password",
        "secret",
        "api_key",
        "credential_key",
        "ciphertext",
        "key_version",
    }
)

_POISONED_METADATA = {
    "signal_id": "sig-1",
    "digest_id": "dig-1",
    "signal_count": 2,
    "summary": "digest summary",
    "connector": "cal",
    "action_type": "approve",
    "approval_count": 1,
    "url": "https://example.test/s",
    "token": "ntfy-secret-token",
    "password": "hunter2",
    "secret": "top-secret",
    "api_key": "sk-live-123",
    "credential_key": "lumogis-credential-key-material",
    "ciphertext": "encrypted-blob",
    "key_version": 3,
}

_ALL_NOTIFICATION_TYPES = list(NotificationType)


def test_in_app_routine_elevation_sse_event_name():
    channel = InAppChannel()
    with patch("routes.events.enqueue_user_sse") as mock_sse:
        channel.deliver(
            TypedNotification(
                user_id="u1",
                notification_type=NotificationType.ROUTINE_ELEVATION,
                title="Lumogis",
                body="Approval required",
                metadata={"connector": "c", "action_type": "a", "approval_count": 1},
                emit_id="e1",
            )
        )
        mock_sse.assert_called_once()
        event_type, data, kwargs = (
            mock_sse.call_args[0][0],
            mock_sse.call_args[0][1],
            mock_sse.call_args[1],
        )
        assert event_type == "routine_elevation_ready"
        assert kwargs["user_id"] == "u1"
        assert data["connector"] == "c"


def _assert_sse_payload_has_no_credential_keys(data: dict) -> None:
    raw = json.dumps(data).lower()
    for key in _FORBIDDEN_SSE_KEYS:
        assert f'"{key}"' not in raw, f"SSE payload leaked forbidden key {key!r}: {data}"


@pytest.mark.parametrize(
    "notification_type,expected_event",
    [
        (NotificationType.SIGNAL_RECEIVED, "signal_received"),
        (NotificationType.SIGNAL_DIGEST, "signal_digest"),
        (NotificationType.ROUTINE_ELEVATION, "routine_elevation_ready"),
        (NotificationType.SECURITY_ALERT, "notification"),
        (NotificationType.ACTION_EXECUTED, "notification"),
        (NotificationType.CONSOLIDATION_DONE, "notification"),
    ],
)
def test_sse_payload_credential_key_guard(notification_type, expected_event):
    notification = TypedNotification(
        user_id="u1",
        notification_type=notification_type,
        tier=NotificationTier.URGENT,
        title="Lumogis",
        body="Body",
        metadata=dict(_POISONED_METADATA),
        emit_id="emit-guard-1",
    )
    event_type, data = _sse_payload(notification)
    assert event_type == expected_event
    _assert_sse_payload_has_no_credential_keys(data)


@pytest.mark.parametrize("notification_type", _ALL_NOTIFICATION_TYPES)
def test_sse_payload_no_top_level_metadata_key(notification_type):
    notification = TypedNotification(
        user_id="u1",
        notification_type=notification_type,
        tier=NotificationTier.URGENT,
        title="Lumogis",
        body="Body",
        metadata=dict(_POISONED_METADATA),
        emit_id="emit-meta-guard-1",
    )
    _event_type, data = _sse_payload(notification)
    assert "metadata" not in data


def test_sse_payload_security_alert_allowlists_operational_fields():
    notification = TypedNotification(
        user_id="u1",
        notification_type=NotificationType.SECURITY_ALERT,
        tier=NotificationTier.URGENT,
        title="Lumogis",
        body="Body",
        metadata={"alert_type": "quota", "resource": "qdrant", "token": "leak"},
        emit_id="emit-allow-1",
    )
    _event_type, data = _sse_payload(notification)
    assert data["alert_type"] == "quota"
    assert data["resource"] == "qdrant"
    assert "token" not in data


def test_sse_payload_action_executed_allowlists_audit_id():
    notification = TypedNotification(
        user_id="u1",
        notification_type=NotificationType.ACTION_EXECUTED,
        tier=NotificationTier.ACTION_REQUIRED,
        title="Lumogis",
        body="Body",
        metadata={"audit_id": "a-1", "reverse_token": "rt-secret"},
        emit_id="emit-allow-2",
    )
    _event_type, data = _sse_payload(notification)
    assert data["audit_id"] == "a-1"
    assert "reverse_token" not in data


def test_sse_payload_consolidation_done_allowlists_summary():
    notification = TypedNotification(
        user_id="u1",
        notification_type=NotificationType.CONSOLIDATION_DONE,
        tier=NotificationTier.BACKGROUND,
        title="Lumogis",
        body="Body",
        metadata={"summary": "done", "job_id": "j-1"},
        emit_id="emit-allow-3",
    )
    _event_type, data = _sse_payload(notification)
    assert data["summary"] == "done"
    assert data["job_id"] == "j-1"


def test_in_app_channel_sse_payload_credential_key_guard():
    channel = InAppChannel()
    with patch("routes.events.enqueue_user_sse") as mock_sse:
        channel.deliver(
            TypedNotification(
                user_id="u1",
                notification_type=NotificationType.SECURITY_ALERT,
                tier=NotificationTier.URGENT,
                title="Lumogis",
                body="Security event",
                metadata=dict(_POISONED_METADATA),
                emit_id="emit-guard-2",
            )
        )
        mock_sse.assert_called_once()
        _event_type, data, _kwargs = (
            mock_sse.call_args[0][0],
            mock_sse.call_args[0][1],
            mock_sse.call_args[1],
        )
        _assert_sse_payload_has_no_credential_keys(data)


def test_document_status_sse_does_not_cancel_wow_debounce(monkeypatch):
    import routes.events as events_mod

    wow_timers: dict[str, object] = {}
    doc_timers: dict[str, object] = {}
    wow_cancelled = []
    doc_cancelled = []

    class _FakeTimer:
        def __init__(self, _delay, _fn):
            self._fn = _fn

        def cancel(self):
            wow_cancelled.append(True)

        def start(self):
            pass

    class _DocTimer:
        def __init__(self, _delay, _fn):
            self._fn = _fn

        def cancel(self):
            doc_cancelled.append(True)

        def start(self):
            pass

    monkeypatch.setattr(events_mod, "_wow_debounce_timers", wow_timers)
    monkeypatch.setattr(events_mod, "_document_debounce_timers", doc_timers)
    monkeypatch.setattr(events_mod.threading, "Timer", _FakeTimer)

    events_mod._schedule_wow_readiness_push("alice")
    assert "alice" in wow_timers

    monkeypatch.setattr(events_mod.threading, "Timer", _DocTimer)
    events_mod._schedule_document_status_push("alice")

    assert wow_cancelled == []
    assert "alice" in doc_timers


def test_document_status_changed_sse_empty_payload():
    from routes.events import _make_sse_event

    msg = _make_sse_event("document_status_changed", {}, user_id="alice")
    assert "event: document_status_changed" in msg
    assert "data: {}" in msg
    _assert_sse_payload_has_no_credential_keys({})
