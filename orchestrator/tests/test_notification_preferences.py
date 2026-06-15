# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Preference store + API service tests."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from models.notifications import ChannelId
from models.notifications import NotificationPreferencePatchItem
from models.notifications import NotificationPreferencesPatch
from models.notifications import NotificationTier
from models.notifications import NotificationType
from services.notifications import migrate_webpush_prefs as seeder
from services.notifications import preferences as prefs_svc

import config


class NotificationPrefsFakeStore:
    def __init__(self):
        self.tier_policy = {
            "urgent": (True, ["ntfy", "web_push", "in_app"]),
            "action_required": (False, ["ntfy", "web_push", "in_app"]),
            "informational": (False, ["ntfy", "web_push", "in_app"]),
            "background": (False, ["in_app"]),
        }
        self.prefs: dict[tuple[str, str, str], bool] = {}
        self.webpush_rows: dict[str, list[dict]] = {}
        self.app_settings: dict[str, str] = {}

    def ping(self) -> bool:
        return True

    def execute(self, query: str, params: tuple | None = None) -> None:
        q = " ".join(query.lower().split())
        params = params or ()
        if "insert into notification_preferences" in q:
            uid, ntype, ch, enabled = params[:4]
            self.prefs[(uid, ntype, ch)] = bool(enabled)
        elif "insert into notification_tier_policy" in q:
            tier, bypass, channels = params
            self.tier_policy[tier] = (bool(bypass), list(channels))
        elif "insert into app_settings" in q:
            self.app_settings[params[0]] = params[1]

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = " ".join(query.lower().split())
        params = params or ()
        if "from app_settings" in q:
            v = self.app_settings.get(params[0])
            return {"value": v} if v is not None else None
        if "from notification_preferences" in q and "select 1" in q:
            key = (params[0], params[1], params[2])
            return {"ok": 1} if key in self.prefs else None
        if "from notification_user_settings" in q:
            return None
        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        q = " ".join(query.lower().split())
        params = params or ()
        if "from notification_tier_policy" in q:
            return [
                {
                    "tier": tier,
                    "bypass_quiet_hours": bypass,
                    "default_channels": channels,
                }
                for tier, (bypass, channels) in self.tier_policy.items()
            ]
        if "from notification_preferences" in q and "user_id" in q:
            uid = params[0]
            return [
                {"notification_type": k[1], "channel": k[2], "enabled": v}
                for k, v in self.prefs.items()
                if k[0] == uid
            ]
        if "from webpush_subscriptions" in q:
            if "distinct user_id" in q:
                return [{"user_id": u} for u in self.webpush_rows]
            uid = params[0]
            return self.webpush_rows.get(uid, [])
        return []

    def close(self) -> None:
        pass

    @contextmanager
    def transaction(self):
        yield


@pytest.fixture
def prefs_store(monkeypatch):
    store = NotificationPrefsFakeStore()
    config.reset_notification_factories()
    config.invalidate_tier_policy_cache()
    monkeypatch.setitem(config._instances, "metadata_store", store)
    yield store
    config.reset_notification_factories()
    config.invalidate_tier_policy_cache()


def test_effective_prefs_use_tier_defaults_when_sparse_empty(prefs_store):
    resp = prefs_svc.get_effective_preferences("u1")
    assert len(resp.types) == len(NotificationType)
    row = next(t for t in resp.types if t.notification_type == NotificationType.SIGNAL_RECEIVED)
    ntfy = next(c for c in row.channels if c.channel == ChannelId.NTFY)
    assert ntfy.enabled is True
    assert ntfy.effective is True


def test_patch_upserts_sparse_row(prefs_store):
    patch = NotificationPreferencesPatch(
        preferences=[
            NotificationPreferencePatchItem(
                notification_type=NotificationType.SIGNAL_RECEIVED,
                channel=ChannelId.NTFY,
                enabled=False,
            )
        ]
    )
    resp = prefs_svc.patch_preferences("u1", patch)
    row = next(t for t in resp.types if t.notification_type == NotificationType.SIGNAL_RECEIVED)
    ntfy = next(c for c in row.channels if c.channel == ChannelId.NTFY)
    assert ntfy.enabled is False
    assert ntfy.effective is False


def test_patch_rejects_enable_outside_tier(prefs_store):
    patch = NotificationPreferencesPatch(
        preferences=[
            NotificationPreferencePatchItem(
                notification_type=NotificationType.CONSOLIDATION_DONE,
                channel=ChannelId.NTFY,
                enabled=True,
            )
        ]
    )
    with pytest.raises(HTTPException) as exc:
        prefs_svc.patch_preferences("u1", patch)
    assert exc.value.status_code == 422


def test_webpush_pref_seeder_preserves_opt_out(prefs_store):
    prefs_store.webpush_rows["u-opt"] = [
        {"notify_on_signals": False, "notify_on_shared_scope": False},
    ]
    seeder.run_if_needed()
    resp = prefs_svc.get_effective_preferences("u-opt")
    sig = next(t for t in resp.types if t.notification_type == NotificationType.SIGNAL_RECEIVED)
    wp = next(c for c in sig.channels if c.channel == ChannelId.WEB_PUSH)
    assert wp.effective is False


def test_webpush_pref_seeder_idempotent(prefs_store):
    prefs_store.webpush_rows["u1"] = [{"notify_on_signals": True, "notify_on_shared_scope": False}]
    seeder.run_if_needed()
    count1 = len(prefs_store.prefs)
    seeder.run_if_needed()
    assert len(prefs_store.prefs) == count1


def test_patch_duplicate_items_last_wins(prefs_store):
    patch = NotificationPreferencesPatch(
        preferences=[
            NotificationPreferencePatchItem(
                notification_type=NotificationType.SIGNAL_RECEIVED,
                channel=ChannelId.NTFY,
                enabled=False,
            ),
            NotificationPreferencePatchItem(
                notification_type=NotificationType.SIGNAL_RECEIVED,
                channel=ChannelId.NTFY,
                enabled=True,
            ),
        ]
    )
    resp = prefs_svc.patch_preferences("u1", patch)
    row = next(t for t in resp.types if t.notification_type == NotificationType.SIGNAL_RECEIVED)
    ntfy = next(c for c in row.channels if c.channel == ChannelId.NTFY)
    assert ntfy.enabled is True


def test_admin_patch_rejects_empty_default_channels(prefs_store):
    from models.notifications import NotificationTierPolicyPatch

    with pytest.raises(HTTPException) as exc:
        prefs_svc.patch_tier_policy(
            NotificationTier.INFORMATIONAL,
            NotificationTierPolicyPatch(default_channels=[]),
            actor_user_id="admin",
        )
    assert exc.value.detail == "tier_policy_invalid_channels"


def test_user_a_cannot_read_user_b_prefs(prefs_store):
    prefs_store.prefs[("user-b", "signal_received", "ntfy")] = False
    resp_a = prefs_svc.get_effective_preferences("user-a")
    resp_b = prefs_svc.get_effective_preferences("user-b")
    row_a = next(t for t in resp_a.types if t.notification_type == NotificationType.SIGNAL_RECEIVED)
    row_b = next(t for t in resp_b.types if t.notification_type == NotificationType.SIGNAL_RECEIVED)
    ntfy_a = next(c for c in row_a.channels if c.channel == ChannelId.NTFY)
    ntfy_b = next(c for c in row_b.channels if c.channel == ChannelId.NTFY)
    assert ntfy_a.enabled is True
    assert ntfy_a.effective is True
    assert ntfy_b.enabled is False
    assert ntfy_b.effective is False


def test_orphan_pref_after_tier_shrink(prefs_store):
    prefs_store.prefs[("u1", "signal_received", "ntfy")] = True
    prefs_store.tier_policy["informational"] = (False, ["in_app"])
    config.invalidate_tier_policy_cache()
    resp = prefs_svc.get_effective_preferences("u1")
    row = next(t for t in resp.types if t.notification_type == NotificationType.SIGNAL_RECEIVED)
    ntfy = next(c for c in row.channels if c.channel == ChannelId.NTFY)
    assert ntfy.enabled is True
    assert ntfy.effective is False
    assert ntfy.mutable is False


def test_webpush_pref_seeder_fail_open_logs_exception(prefs_store, monkeypatch, caplog):
    """ADR 077/098: seeder errors must not abort boot — log at error and continue."""
    import logging

    prefs_store.webpush_rows["u1"] = [{"notify_on_signals": True, "notify_on_shared_scope": False}]
    orig_fetch_all = prefs_store.fetch_all

    def fetch_all_with_webpush_failure(query, params=()):
        if "webpush_subscriptions" in " ".join(query.lower().split()):
            raise RuntimeError("simulated seeder DB error")
        return orig_fetch_all(query, params)

    monkeypatch.setattr(prefs_store, "fetch_all", fetch_all_with_webpush_failure)

    with caplog.at_level(logging.ERROR, logger="services.notifications.migrate_webpush_prefs"):
        seeder.run_if_needed()

    assert not prefs_store.app_settings.get(seeder._SEEDER_FLAG_KEY)
    assert any("webpush_prefs_seeder: failed" in r.getMessage() for r in caplog.records)


def test_startup_reaches_serving_state_when_webpush_seeder_fails(monkeypatch):
    """Lifespan must complete and /healthz must respond when the seeder errors."""
    import main
    from fastapi.testclient import TestClient

    ms = config.get_metadata_store()
    orig_fetch_all = ms.fetch_all

    def fetch_all_with_webpush_failure(query, params=()):
        if "webpush_subscriptions" in " ".join(query.lower().split()):
            raise RuntimeError("simulated seeder failure at startup")
        return orig_fetch_all(query, params)

    monkeypatch.setattr(ms, "fetch_all", fetch_all_with_webpush_failure)

    logged: list[str] = []
    real_exception = seeder._log.exception

    def capture_exception(msg, *args, **kwargs):
        logged.append(str(msg))
        return real_exception(msg, *args, **kwargs)

    monkeypatch.setattr(seeder._log, "exception", capture_exception)

    with TestClient(main.app) as client:
        resp = client.get("/healthz")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert any("webpush_prefs_seeder: failed" in m for m in logged)
