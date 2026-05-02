# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``GET /api/v1/me/notifications`` — read-only notification façade (Phase 4)."""

from __future__ import annotations

import json
from datetime import datetime
from datetime import timezone
from unittest.mock import patch

import pytest
from connectors.registry import NTFY
from fastapi.testclient import TestClient
from services.me_notifications import WEB_PUSH_CHANNEL_ID

from services import connector_credentials as ccs


@pytest.fixture(autouse=True)
def _me_notifications_default_single_user_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Most routes here expect anonymous dev access unless a test opts into JWT mode."""
    monkeypatch.setenv("AUTH_ENABLED", "false")


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


def _auth_header(
    monkeypatch: pytest.MonkeyPatch, user_id: str, role: str = "user"
) -> dict[str, str]:
    monkeypatch.setenv("AUTH_SECRET", "test-me-ntfy-secret-do-not-use")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    from auth import mint_access_token

    tok = mint_access_token(user_id, role)
    return {"Authorization": f"Bearer {tok}"}


def test_me_notifications_401_when_auth_enabled_without_token(client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-me-ntfy-401")
    r = client.get("/api/v1/me/notifications")
    assert r.status_code == 401


def test_me_notifications_200_authenticated_when_auth_enabled(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "alice-ntfy-1", "user")
    r = client.get("/api/v1/me/notifications", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    assert "channels" in body and "summary" in body
    assert body["summary"]["total"] == len(body["channels"])


def test_me_notifications_200_default_user_when_auth_disabled(client) -> None:
    r = client.get("/api/v1/me/notifications")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["channels"], list)
    assert body["summary"]["total"] == len(body["channels"])


def test_me_notifications_known_channels_stable_order(client) -> None:
    from services.me_notifications import notification_channel_ids

    r = client.get("/api/v1/me/notifications")
    assert r.status_code == 200
    ids = [ch["connector"] for ch in r.json()["channels"]]
    assert ids == list(notification_channel_ids())


def test_me_notifications_safe_json_no_secret_material(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "safe-ntfy-user", "user")
    r = client.get("/api/v1/me/notifications", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    ch_keys = {
        "connector",
        "label",
        "description",
        "configured",
        "active_tier",
        "user_credential_present",
        "household_credential_available",
        "system_credential_available",
        "env_fallback_available",
        "url",
        "url_configured",
        "topic_configured",
        "token_configured",
        "updated_at",
        "key_version",
        "subscription_count",
        "push_service_configured",
        "status",
        "why_not_available",
    }
    sum_keys = {"total", "configured", "not_configured", "by_active_tier"}
    assert set(body["summary"].keys()) == sum_keys
    for ch in body["channels"]:
        assert set(ch.keys()) == ch_keys

    raw = json.dumps(body)
    lowered = raw.lower()
    assert "ciphertext" not in lowered
    assert "bearer " not in lowered
    assert '"p256dh"' not in lowered
    assert '"endpoint"' not in lowered


def test_me_notifications_summary_counts(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "summary-ntfy-user", "user")
    r = client.get("/api/v1/me/notifications", headers=hdr)
    assert r.status_code == 200
    b = r.json()
    s = b["summary"]
    assert s["total"] == len(b["channels"])
    assert s["configured"] + s["not_configured"] == s["total"]
    assert sum(s["by_active_tier"].values()) == s["total"]


def test_me_notifications_ntfy_user_tier_metadata_only(client, monkeypatch) -> None:
    uid = "ntfy-meta-user"
    hdr = _auth_header(monkeypatch, uid, "user")
    ts = datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)
    rec = ccs.CredentialRecord(
        user_id=uid,
        connector=NTFY,
        created_at=ts,
        updated_at=ts,
        created_by="self",
        updated_by="self",
        key_version=1,
    )

    def _get(u: str, c: str):
        return rec if (u, c) == (uid, NTFY) else None

    with (
        patch("services.me_notifications.ccs.get_record", _get),
        patch("services.me_notifications.ct.household_get_record", lambda _c: None),
        patch("services.me_notifications.ct.system_get_record", lambda _c: None),
        patch("services.me_notifications._ntfy_env_fallback_available", lambda: False),
    ):
        r = client.get("/api/v1/me/notifications", headers=hdr)
    assert r.status_code == 200
    ntfy = next(c for c in r.json()["channels"] if c["connector"] == NTFY)
    assert ntfy["configured"] is True
    assert ntfy["active_tier"] == "user"
    assert ntfy["topic_configured"] is None
    assert ntfy["token_configured"] is None
    assert ntfy["url_configured"] is None


def test_me_notifications_ntfy_env_fallback_no_secret_values(client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    secret_topic = "my-secret-topic-name"
    secret_tok = "tk_super_secret_123"
    monkeypatch.setenv("NTFY_TOPIC", secret_topic)
    monkeypatch.setenv("NTFY_TOKEN", secret_tok)
    monkeypatch.setenv("NTFY_URL", "https://ntfy.example.com")
    try:
        r = client.get("/api/v1/me/notifications")
        assert r.status_code == 200
        ntfy = next(c for c in r.json()["channels"] if c["connector"] == NTFY)
        assert ntfy["active_tier"] == "env"
        assert ntfy["topic_configured"] is True
        assert ntfy["token_configured"] is True
        raw = json.dumps(r.json())
        assert secret_topic not in raw
        assert secret_tok not in raw
        assert "ntfy.example.com" in raw
    finally:
        monkeypatch.delenv("NTFY_TOPIC", raising=False)
        monkeypatch.delenv("NTFY_TOKEN", raising=False)
        monkeypatch.delenv("NTFY_URL", raising=False)


def test_me_notifications_web_push_counts_and_vapid(client, monkeypatch) -> None:
    uid = "webpush-user"
    monkeypatch.setenv("WEBPUSH_VAPID_PUBLIC_KEY", "pub-demo")
    monkeypatch.setenv("WEBPUSH_VAPID_PRIVATE_KEY", "priv-demo")

    class _MS:
        def fetch_one(self, query: str, params: tuple) -> dict | None:
            q = query.lower()
            if "webpush_subscriptions" in q and "count" in q:
                return {"n": 2}
            return None

    hdr = _auth_header(monkeypatch, uid, "user")
    with patch("services.me_notifications.config.get_metadata_store", lambda: _MS()):
        r = client.get("/api/v1/me/notifications", headers=hdr)
    assert r.status_code == 200
    wp = next(c for c in r.json()["channels"] if c["connector"] == WEB_PUSH_CHANNEL_ID)
    assert wp["subscription_count"] == 2
    assert wp["push_service_configured"] is True
    assert wp["configured"] is True
