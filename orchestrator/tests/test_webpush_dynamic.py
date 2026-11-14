# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-648 — dynamic-body web push (send_dynamic_to_user) + channel branch.

Proves the real wire contract (``{title, body, url}`` only, via the
``build_safe_push_json_from_dict`` choke point), the ``notify_on_signals`` opt-in
gate, and that the fixed-template path (ROUTINE_ELEVATION) is unaffected.
"""

from __future__ import annotations

import json
import threading

import pytest


@pytest.fixture
def vp_env(monkeypatch):
    monkeypatch.setenv("WEBPUSH_VAPID_PRIVATE_KEY", "test-private-key-pem-ish")
    monkeypatch.setenv("WEBPUSH_VAPID_SUBJECT", "mailto:web-push-test@lumogis.invalid")


@pytest.fixture(autouse=True)
def sync_webpush_executor(monkeypatch):
    """Run the async web-push executor inline so payload-capture assertions are
    deterministic. The genuine non-blocking behaviour is proven separately in
    test_dynamic_branch_dispatches_async_and_does_not_block with a REAL executor."""
    import services.webpush as w

    class _Syn:
        def submit(self, fn, *a, **k):
            fn(*a, **k)

    monkeypatch.setattr(w, "_executor", _Syn())


class _MiniStore:
    def __init__(self, rows):
        self.rows = rows

    def fetch_one(self, *a, **k):
        return None

    def fetch_all(self, query, params):
        ql = query.lower()
        if "webpush_subscriptions" in ql and "where user_id" in ql:
            uid = params[0]
            return [
                {
                    "id": r["id"],
                    "endpoint": r["endpoint"],
                    "p256dh": r["p256dh"],
                    "auth": r["auth"],
                    "notify_on_signals": r["notify_on_signals"],
                    "notify_on_shared_scope": r["notify_on_shared_scope"],
                }
                for r in self.rows
                if r["user_id"] == uid
            ]
        return []

    def execute(self, query, params=None):
        return None

    def close(self):
        pass


def _row(uid, sid, endpoint, *, notify_on_signals):
    return {
        "user_id": uid,
        "id": sid,
        "endpoint": endpoint,
        "p256dh": "p256",
        "auth": "au",
        "notify_on_signals": notify_on_signals,
        "notify_on_shared_scope": True,
    }


@pytest.fixture
def fake_store(monkeypatch):
    import config as cfg

    store = _MiniStore(rows=[])
    monkeypatch.setitem(cfg._instances, "metadata_store", store)
    yield store
    cfg._instances.pop("metadata_store", None)


def test_dynamic_body_wire_contract_is_title_body_url(vp_env, fake_store, monkeypatch):
    import services.webpush as w

    captured: list[str] = []

    def fake_push(*, subscription_info, data: bytes):
        captured.append(data.decode("utf-8"))

    monkeypatch.setattr(w, "_dispatch_web_push", fake_push)
    fake_store.rows = [_row("alice", 1, "https://alice/", notify_on_signals=True)]

    result = w.send_dynamic_to_user("alice", "Landlord needs you", "Rent due", "/email")
    assert result.sent == 1

    payload = json.loads(captured[0])
    assert payload == {"title": "Landlord needs you", "body": "Rent due", "url": "/email"}
    # No tag / targetPath / extras on the wire.
    assert set(payload.keys()) == {"title", "body", "url"}


def test_dynamic_body_skipped_when_opt_out(vp_env, fake_store, monkeypatch):
    import services.webpush as w

    calls: list[str] = []
    monkeypatch.setattr(
        w, "_dispatch_web_push", lambda **kw: calls.append(kw["subscription_info"]["endpoint"])
    )
    fake_store.rows = [
        _row("alice", 1, "https://a1/", notify_on_signals=False),
        _row("alice", 2, "https://a2/", notify_on_signals=True),
    ]

    result = w.send_dynamic_to_user("alice", "t", "b", "/email")
    # Only the opted-in subscription is targeted.
    assert calls == ["https://a2/"]
    assert result.sent == 1


def test_dynamic_body_rejects_extra_keys():
    import services.webpush as w

    with pytest.raises(ValueError):
        w.build_safe_push_json_from_dict({"title": "t", "body": "b", "url": "/email", "tag": "x"})


def test_channel_dynamic_branch_routes_needs_you(vp_env, fake_store, monkeypatch):
    import services.webpush as w
    from models.notifications import NotificationTier
    from models.notifications import NotificationType
    from models.notifications import TypedNotification
    from services.notifications.channels.web_push_channel import WebPushChannel

    captured: list[str] = []
    monkeypatch.setattr(w, "_dispatch_web_push", lambda **kw: captured.append(kw["data"].decode()))
    fake_store.rows = [_row("alice", 1, "https://a/", notify_on_signals=True)]

    res = WebPushChannel().deliver(
        TypedNotification(
            user_id="alice",
            notification_type=NotificationType.SIGNAL_NEEDS_YOU,
            tier=NotificationTier.ACTION_REQUIRED,
            title="Landlord needs you",
            body="Rent due",
            metadata={"target_path": "/email"},
        )
    )
    assert res.status == "delivered"
    assert json.loads(captured[0]) == {
        "title": "Landlord needs you",
        "body": "Rent due",
        "url": "/email",
    }


def test_channel_dynamic_branch_vapid_guard(fake_store, monkeypatch):
    # VAPID unset → the new branch must skip (not call pywebpush) — Sonnet R2 P2.
    import services.webpush as w
    from models.notifications import NotificationType
    from models.notifications import TypedNotification
    from services.notifications.channels.web_push_channel import WebPushChannel

    monkeypatch.delenv("WEBPUSH_VAPID_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("WEBPUSH_VAPID_SUBJECT", raising=False)
    called = []
    monkeypatch.setattr(w, "_dispatch_web_push", lambda **kw: called.append(1))
    fake_store.rows = [_row("alice", 1, "https://a/", notify_on_signals=True)]

    res = WebPushChannel().deliver(
        TypedNotification(
            user_id="alice",
            notification_type=NotificationType.SIGNAL_MORNING_BRIEF,
            title="Morning brief",
            body="2 emails need you today",
            metadata={"target_path": "/email"},
        )
    )
    assert res.status == "skipped"
    assert res.reason == "vapid_not_configured"
    assert called == []


def test_routine_elevation_template_unaffected_by_signals_gate(vp_env, fake_store, monkeypatch):
    import services.webpush as w
    from models.notifications import NotificationType
    from models.notifications import TypedNotification
    from services.notifications.channels.web_push_channel import WebPushChannel

    captured: list[str] = []
    monkeypatch.setattr(w, "_dispatch_web_push", lambda **kw: captured.append(kw["data"].decode()))
    # notify_on_signals FALSE must NOT block the approval template.
    fake_store.rows = [_row("alice", 1, "https://a/", notify_on_signals=False)]

    res = WebPushChannel().deliver(
        TypedNotification(
            user_id="alice",
            notification_type=NotificationType.ROUTINE_ELEVATION,
            title="Lumogis",
            body="Approval required",
        )
    )
    assert res.status == "delivered"
    assert json.loads(captured[0])["url"] == "/approvals"


def test_dynamic_branch_dispatches_async_and_does_not_block(vp_env, fake_store, monkeypatch):
    # LUM-662: the needs-you web-push send must NOT block the caller (ingest path)
    # on slow network I/O. deliver() returns while the pywebpush call is still
    # in-flight on the real executor.
    from concurrent.futures import ThreadPoolExecutor

    import services.webpush as w
    from models.notifications import NotificationType
    from models.notifications import TypedNotification
    from services.notifications.channels.web_push_channel import WebPushChannel

    real_executor = ThreadPoolExecutor(max_workers=2)
    monkeypatch.setattr(w, "_executor", real_executor)  # override the autouse sync stub

    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def slow_send(*, subscription_info, data):
        started.set()
        release.wait(2)
        finished.set()

    monkeypatch.setattr(w, "_dispatch_web_push", slow_send)
    fake_store.rows = [_row("alice", 1, "https://a/", notify_on_signals=True)]

    try:
        res = WebPushChannel().deliver(
            TypedNotification(
                user_id="alice",
                notification_type=NotificationType.SIGNAL_NEEDS_YOU,
                title="Landlord needs you",
                body="Rent due",
                metadata={"target_path": "/email"},
            )
        )
        # deliver() returned "delivered" (dispatched) while the send is still blocked.
        assert res.status == "delivered"
        assert started.wait(2), "the send should have started on a background thread"
        assert not finished.is_set(), "deliver() must NOT wait for the slow network send"
        release.set()
        assert finished.wait(2)
    finally:
        release.set()
        real_executor.shutdown(wait=True)
