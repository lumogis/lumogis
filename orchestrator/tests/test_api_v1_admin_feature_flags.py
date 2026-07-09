# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``GET /api/v1/admin/feature-flags`` — admin feature-flag visibility (LUM-126)."""

from __future__ import annotations

import features
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


def _auth_header(
    monkeypatch: pytest.MonkeyPatch, user_id: str, role: str = "admin"
) -> dict[str, str]:
    monkeypatch.setenv("AUTH_SECRET", "test-feature-flags-access-secret-do-not-use")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    from auth import mint_access_token

    tok = mint_access_token(user_id, role)
    return {"Authorization": f"Bearer {tok}"}


def test_feature_flags_401_when_auth_enabled_without_token(client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-feature-flags-401-secret")
    r = client.get("/api/v1/admin/feature-flags")
    assert r.status_code == 401


def test_feature_flags_403_non_admin(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "bob", "user")
    r = client.get("/api/v1/admin/feature-flags", headers=hdr)
    assert r.status_code == 403


def test_feature_flags_200_admin_all_disabled_by_default(client, monkeypatch) -> None:
    for key in features.all_flag_keys():
        monkeypatch.delenv(features.get_flag(key).resolved_env_var(), raising=False)
    hdr = _auth_header(monkeypatch, "admin-1", "admin")
    r = client.get("/api/v1/admin/feature-flags", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == len(features.all_flag_keys())
    assert body["enabled"] == 0
    keys = [f["key"] for f in body["flags"]]
    assert keys == features.all_flag_keys()  # sorted, complete
    for f in body["flags"]:
        assert f["enabled"] is False
        assert f["default"] is False
        assert f["env_var"].startswith(features.FLAG_ENV_PREFIX)
        assert f["description"]


def test_feature_flags_reflect_enabled_env(client, monkeypatch) -> None:
    target = features.all_flag_keys()[0]
    monkeypatch.setenv(features.get_flag(target).resolved_env_var(), "true")
    hdr = _auth_header(monkeypatch, "admin-2", "admin")
    r = client.get("/api/v1/admin/feature-flags", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] >= 1
    by_key = {f["key"]: f for f in body["flags"]}
    assert by_key[target]["enabled"] is True


def test_feature_flags_200_when_auth_disabled(client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    r = client.get("/api/v1/admin/feature-flags")
    assert r.status_code == 200


def test_feature_flags_no_secret_like_keys(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "admin-3", "admin")
    r = client.get("/api/v1/admin/feature-flags", headers=hdr)
    assert r.status_code == 200
    forbidden = frozenset(
        {"api_key", "password", "ciphertext", "access_token", "refresh_token", "authorization"}
    )

    def walk(obj: object) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                kl = str(k).lower()
                assert kl not in forbidden
                assert not kl.endswith("_secret")
                walk(v)
        elif isinstance(obj, list):
            for i in obj:
                walk(i)

    walk(r.json())
