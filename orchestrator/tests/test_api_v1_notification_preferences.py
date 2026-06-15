# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``/api/v1/me/notification-preferences`` and admin tier-policy routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _stub_startup_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """TestClient lifespan enqueues ingest jobs; default MockMetadataStore cannot INSERT."""
    monkeypatch.setattr("services.batch_queue.enqueue", lambda **_kwargs: "test-job-id")
    monkeypatch.setattr("services.ingest.enqueue_initial_ingest_scan", lambda: False)


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


def _auth_header(monkeypatch: pytest.MonkeyPatch, user_id: str, role: str = "user") -> dict[str, str]:
    monkeypatch.setenv("AUTH_SECRET", "test-notification-prefs-secret")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    from auth import mint_access_token

    tok = mint_access_token(user_id, role)
    return {"Authorization": f"Bearer {tok}"}


def test_notification_prefs_get_401_when_auth_enabled_without_token(client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-notification-prefs-get-401")
    r = client.get("/api/v1/me/notification-preferences")
    assert r.status_code == 401


def test_notification_prefs_patch_401_when_auth_enabled_without_token(client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-notification-prefs-patch-401")
    r = client.patch(
        "/api/v1/me/notification-preferences",
        json={"preferences": []},
    )
    assert r.status_code == 401


def test_notification_tier_policy_patch_403_non_admin(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "bob", "user")
    r = client.patch(
        "/api/v1/admin/notification-tier-policy/informational",
        headers=hdr,
        json={"default_channels": ["in_app"]},
    )
    assert r.status_code == 403


def test_admin_patch_rejects_unknown_channel(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "admin", "admin")
    r = client.patch(
        "/api/v1/admin/notification-tier-policy/informational",
        headers=hdr,
        json={"default_channels": ["not_a_real_channel"]},
    )
    assert r.status_code == 422
