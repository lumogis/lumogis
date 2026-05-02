# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Phase 4A — orchestrator/services/webpush.py sender + payloads + pruning."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def vp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBPUSH_VAPID_PRIVATE_KEY", "test-private-key-pem-ish")
    monkeypatch.setenv("WEBPUSH_VAPID_SUBJECT", "mailto:web-push-test@lumogis.invalid")


@pytest.fixture
def fake_store(monkeypatch: pytest.MonkeyPatch):
    """In-memory Postgres stand-in keyed like ``postgres_store`` lookups."""
    store = _MiniStore(rows=[])

    import config as cfg

    monkeypatch.setitem(cfg._instances, "metadata_store", store)
    yield store
    cfg._instances.pop("metadata_store", None)


class _MiniStore:
    def __init__(self, rows: list):
        self.rows = rows

    def ping(self) -> bool:
        return True

    def fetch_one(self, *args: object, **kwargs: object) -> dict | None:
        return None

    def fetch_all(self, query: str, params: tuple) -> list:
        ql = query.lower()
        if (
            "webpush_subscriptions" in ql
            and "where user_id" in ql
            and "%s" in query
            and "created_at" not in ql
        ):
            uid = params[0]
            rows = []
            for r in self.rows:
                if r["user_id"] == uid:
                    rows.append(
                        {
                            "id": r["id"],
                            "endpoint": r["endpoint"],
                            "p256dh": r["p256dh"],
                            "auth": r["auth"],
                            "notify_on_signals": r["notify_on_signals"],
                            "notify_on_shared_scope": r["notify_on_shared_scope"],
                        }
                    )
            return rows
        return []

    def execute(self, query: str, params: tuple | None = None) -> None:
        q = " ".join(query.split()).lower()
        params = params or ()

        if q.startswith("update webpush_subscriptions set last_error = null where id = %s"):
            sid = int(params[0])
            for row in self.rows:
                if row["id"] == sid:
                    row["last_error"] = None
            return

        if q.startswith("update webpush_subscriptions set last_error = %s where id = %s"):
            err, sid = params[0], int(params[1])
            for row in self.rows:
                if row["id"] == sid:
                    row["last_error"] = err
            return

        if q.startswith("delete from webpush_subscriptions where id = %s") and len(params) == 1:
            sid = int(params[0])
            self.rows = [r for r in self.rows if r["id"] != sid]

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def sync_webpush_executor(monkeypatch):
    import services.webpush as w

    class _Syn:
        def submit(self, fn):  # noqa: ANN001
            fn()

    monkeypatch.setattr(w, "_executor", _Syn())


def _row(uid: str, sid: int, endpoint: str) -> dict:
    return {
        "user_id": uid,
        "id": sid,
        "endpoint": endpoint,
        "p256dh": "p256",
        "auth": "au",
        "notify_on_signals": False,
        "notify_on_shared_scope": True,
        "last_error": None,
    }


def test_missing_vapid_returns_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    import services.webpush as w

    monkeypatch.delenv("WEBPUSH_VAPID_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("WEBPUSH_VAPID_SUBJECT", raising=False)

    result = w.send_templates_to_user(
        "u1",
        w.WebPushTemplate.APPROVAL_REQUIRED,
    )
    assert result.sent == result.failed == result.pruned == 0
    assert result.disabled_reason == "webpush_vapid_incomplete"


def test_no_subscriptions_empty_result(vp_env: None, fake_store: _MiniStore, monkeypatch) -> None:
    import services.webpush as w

    monkeypatch.setattr(w, "_dispatch_web_push", lambda **kw: None)
    fake_store.rows = []

    result = w.send_templates_to_user("u_empty", w.WebPushTemplate.DEV_TEST)
    assert result.sent == result.failed == result.pruned == 0


def test_sends_only_for_target_user(vp_env: None, fake_store: _MiniStore, monkeypatch) -> None:
    import services.webpush as w

    endpoints: list[str] = []

    def fake_push(*, subscription_info, data: bytes):  # noqa: ANN003
        endpoints.append(subscription_info["endpoint"])
        raise RuntimeError("no network")

    monkeypatch.setattr(w, "_dispatch_web_push", fake_push)
    fake_store.rows = [
        _row("alice", 1, "https://alice/"),
        _row("bob", 2, "https://bob/"),
    ]

    result = w.send_templates_to_user("alice", w.WebPushTemplate.APPROVAL_REQUIRED)
    assert endpoints == ["https://alice/"]
    assert result.sent == 0
    assert result.failed == 1


def test_410_prunes(vp_env: None, fake_store: _MiniStore, monkeypatch) -> None:
    import services.webpush as w

    try:
        from pywebpush import WebPushException
    except ImportError:
        pytest.skip("pywebpush not installed")

    class _Rsp:
        status_code = 410

    def boom(*_, **__) -> None:
        exc = WebPushException("gone")
        exc.response = _Rsp()
        raise exc

    monkeypatch.setattr(w, "_dispatch_web_push", boom)
    fake_store.rows = [_row("gone", 99, "https://gone/")]

    result = w.send_templates_to_user("gone", w.WebPushTemplate.DEV_TEST)
    assert result.pruned == 1
    assert fake_store.rows == []


def test_non_prune_updates_last_error(vp_env: None, fake_store: _MiniStore, monkeypatch) -> None:
    import services.webpush as w

    class _Rsp:
        status_code = 500

    class _Exc(Exception):
        def __init__(self) -> None:
            self.response = _Rsp()

    def boom(*_, **__) -> None:
        raise _Exc()

    monkeypatch.setattr(w, "_dispatch_web_push", boom)
    fake_store.rows = [_row("bad", 1, "https://bad/")]

    result = w.send_templates_to_user("bad", w.WebPushTemplate.DEV_TEST)
    assert result.failed == 1
    assert fake_store.rows[0].get("last_error") == "http_500"


def test_safe_payload_builtin_templates_only() -> None:
    import services.webpush as w

    blob = json.loads(w.build_web_push_payload(w.WebPushTemplate.APPROVAL_REQUIRED))
    assert blob == {
        "title": "Lumogis",
        "body": "Approval required",
        "url": "/approvals",
    }
    blob2 = json.loads(w.build_web_push_payload(w.WebPushTemplate.DEV_TEST))
    assert blob2["body"] == "Test from Lumogis"


def test_safe_builder_rejects_injected_fields() -> None:
    import services.webpush as w

    with pytest.raises(ValueError):
        w.build_safe_push_json_from_dict(
            {
                "title": "ok",
                "body": "ok",
                "url": "/",
                "secret": "nope",
            }
        )


def test_hook_ignores_raw_kwargs(vp_env: None, fake_store: _MiniStore, monkeypatch) -> None:
    import services.webpush as w

    captured: dict[str, bytes] = {}

    def fake_push(*, subscription_info, data: bytes):  # noqa: ANN001
        captured["data"] = data

    monkeypatch.setattr(w, "_dispatch_web_push", fake_push)
    fake_store.rows = [_row("u9", 1, "https://e/")]

    w._on_routine_elevation_ready_web_push(
        user_id="u9",
        connector="my_secret_workspace connector",
        action_type="leak_attempt_action",
        approval_count=99,
    )
    blob = json.loads(captured["data"].decode())
    dumped = json.dumps(blob)
    assert "secret_workspace" not in dumped
    assert "leak_attempt" not in dumped
    assert blob["body"] == "Approval required"


def test_routine_ignore_notify_prefs(vp_env: None, fake_store: _MiniStore, monkeypatch) -> None:
    """Approval template does not honour notify_* (reserved for signal future)."""
    import services.webpush as w

    monkeypatch.setattr(w, "_dispatch_web_push", lambda **_k: None)

    fake_store.rows.append(
        {
            "user_id": "u_prefs",
            "id": 2,
            "endpoint": "https://z/",
            "p256dh": "p256",
            "auth": "au",
            "notify_on_signals": False,
            "notify_on_shared_scope": False,
            "last_error": None,
        },
    )

    assert w.send_templates_to_user("u_prefs", w.WebPushTemplate.APPROVAL_REQUIRED).sent == 1
