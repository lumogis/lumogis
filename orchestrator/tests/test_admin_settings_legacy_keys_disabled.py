# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for legacy global LLM API key gating in admin settings.

Plan ``llm_provider_keys_per_user_migration`` Pass 3.11 pins the
following contract on ``PUT /api/v1/admin/settings`` and on the
``api_key_status`` field returned by ``GET /api/v1/admin/settings``:

* Under ``AUTH_ENABLED=true``:
  - ``api_key_status`` is **omitted entirely** from the GET response (per
    user instruction, NOT repurposed into a household aggregate).
  - PUT with a non-empty ``api_keys`` body returns ``422`` with
    ``detail.code == "legacy_global_api_keys_disabled"`` and a message
    pointing at the per-user routes. No ``app_settings`` write happens.
  - PUT with ``api_keys: {}`` (empty dict) is a no-op (no 422), so
    clients that always include the field can save other settings.
* Under ``AUTH_ENABLED=false``:
  - Legacy behaviour preserved end-to-end: ``api_key_status`` is present
    in the GET response, and PUTs with ``api_keys: {...}`` write to
    ``app_settings`` exactly as before.

The dashboard relies on the **absence** of ``api_key_status`` (rather
than a new flag) to decide whether to render the legacy form, so the
GET-side assertion is the contract that protects the UI.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager

import jwt
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from tests.test_auth_phase1 import FakeUsersStore  # noqa: E402


_TEST_FERNET_KEY = "OlGLYckGIbBSt54y8XVmgb441LgKJWvvYoHnpQ_cv9A="


class _SettingsFakeStore(FakeUsersStore):
    """Tiny store that backs ``put_settings`` / ``get_setting`` from
    ``services/app_settings.py`` so the legacy auth-off path actually
    persists. Inherits the FakeUsersStore so JWT auth works.
    """

    def __init__(self) -> None:
        super().__init__()
        self.app_settings: dict[str, str] = {}

    @staticmethod
    def _norm(query: str) -> str:
        return " ".join(query.split()).lower()

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = self._norm(query)
        p = params or ()
        if q.startswith("select value from app_settings where key"):
            (key,) = p
            v = self.app_settings.get(key)
            return {"value": v} if v is not None else None
        return super().fetch_one(query, params)

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        return super().fetch_all(query, params)

    def execute(self, query: str, params: tuple | None = None):  # noqa: ANN001
        q = self._norm(query)
        if "insert into app_settings" in q and "on conflict" in q:
            key, value = params  # type: ignore[misc]
            self.app_settings[key] = value
            return None
        return super().execute(query, params)


@pytest.fixture
def store(monkeypatch):
    import config as _config
    from services import connector_credentials as ccs

    s = _SettingsFakeStore()
    _config._instances["metadata_store"] = s
    ccs.reset_for_tests()
    yield s
    _config._instances.pop("metadata_store", None)
    ccs.reset_for_tests()


@pytest.fixture
def auth_on_env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-admin-settings-legacy-disabled-access")
    monkeypatch.setenv(
        "LUMOGIS_JWT_REFRESH_SECRET",
        "test-admin-settings-legacy-disabled-refresh",
    )
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("REFRESH_TOKEN_TTL_SECONDS", "3600")
    monkeypatch.setenv("LUMOGIS_REFRESH_COOKIE_SECURE", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", _TEST_FERNET_KEY)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    yield
    from routes.auth import _reset_rate_limit_for_tests
    _reset_rate_limit_for_tests()


@pytest.fixture
def auth_off_env(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    yield


def _mint_admin_jwt(user_id: str) -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "role": "admin",
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        },
        os.environ["AUTH_SECRET"],
        algorithm="HS256",
    )


@contextmanager
def _client():
    import main
    with TestClient(main.app) as client:
        yield client


def _seed_admin(store) -> str:
    import services.users as users_svc
    if users_svc.get_user_by_email("admin@home.lan") is None:
        users_svc.create_user("admin@home.lan", "verylongpassword12", "admin")
    user = users_svc.get_user_by_email("admin@home.lan")
    assert user is not None
    return user.id


# ---------------------------------------------------------------------------
# Auth-on: GET omits api_key_status; PUT api_keys={...} → 422; no write happens
# ---------------------------------------------------------------------------


def test_get_settings_under_auth_on_omits_api_key_status(store, auth_on_env):
    admin = _seed_admin(store)
    hdr = {"Authorization": f"Bearer {_mint_admin_jwt(admin)}"}
    with _client() as client:
        resp = client.get("/settings", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "api_key_status" not in body, (
        "api_key_status MUST be omitted under AUTH_ENABLED=true so the "
        "dashboard knows to render the per-user 'My LLM keys' panel "
        "instead of the legacy global form (per Pass 3.11 contract)."
    )
    assert "models" in body  # other fields unchanged
    assert "default_model" in body


def test_put_settings_with_api_keys_under_auth_on_returns_422(
    store, auth_on_env
):
    admin = _seed_admin(store)
    hdr = {"Authorization": f"Bearer {_mint_admin_jwt(admin)}"}
    with _client() as client:
        resp = client.put(
            "/settings",
            headers=hdr,
            json={"api_keys": {"ANTHROPIC_API_KEY": "sk-test-secret"}},
        )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    detail = body.get("detail")
    assert isinstance(detail, dict), f"expected dict detail, got {detail!r}"
    assert detail.get("code") == "legacy_global_api_keys_disabled"
    assert "AUTH_ENABLED=true" in detail.get("message", "")
    assert "/api/v1/me/connector-credentials/" in detail.get("message", "")
    # No app_settings write should have happened — assert the key is not
    # in the in-memory store. (FakeUsersStore.app_settings starts empty.)
    assert "ANTHROPIC_API_KEY" not in store.app_settings


def test_put_settings_with_empty_api_keys_under_auth_on_is_no_op(
    store, auth_on_env
):
    """Empty ``api_keys: {}`` must NOT trigger 422 — clients send other fields."""
    admin = _seed_admin(store)
    hdr = {"Authorization": f"Bearer {_mint_admin_jwt(admin)}"}
    with _client() as client:
        resp = client.put(
            "/settings",
            headers=hdr,
            json={"api_keys": {}, "default_model": "llama"},
        )
    # Either 200 (with default_model accepted) or 400 (model unknown to YAML)
    # — what we assert is "NOT a 422 with legacy_global_api_keys_disabled".
    assert resp.status_code != 422 or (
        resp.json().get("detail", {}).get("code")
        != "legacy_global_api_keys_disabled"
    ), resp.text


def test_put_settings_omitted_api_keys_under_auth_on_is_no_op(
    store, auth_on_env
):
    """``api_keys`` field omitted entirely must not trigger 422."""
    admin = _seed_admin(store)
    hdr = {"Authorization": f"Bearer {_mint_admin_jwt(admin)}"}
    with _client() as client:
        resp = client.put(
            "/settings",
            headers=hdr,
            json={"reranker_enabled": False},
        )
    assert resp.status_code != 422 or (
        resp.json().get("detail", {}).get("code")
        != "legacy_global_api_keys_disabled"
    ), resp.text


# ---------------------------------------------------------------------------
# Auth-off: legacy behaviour preserved
# ---------------------------------------------------------------------------


def test_get_settings_under_auth_off_includes_api_key_status(
    store, auth_off_env
):
    with _client() as client:
        resp = client.get("/settings")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "api_key_status" in body, (
        "api_key_status MUST remain present under AUTH_ENABLED=false so "
        "the legacy single-user dashboard form keeps working."
    )
    assert isinstance(body["api_key_status"], dict)


def test_put_settings_with_api_keys_under_auth_off_writes_app_settings(
    store, auth_off_env
):
    with _client() as client:
        resp = client.put(
            "/settings",
            json={"api_keys": {"ANTHROPIC_API_KEY": "sk-legacy-test"}},
        )
    assert resp.status_code == 200, resp.text
    assert store.app_settings.get("ANTHROPIC_API_KEY") == "sk-legacy-test"
