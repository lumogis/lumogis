# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for cloud LLM privacy mode (LUM-194)."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest
from models.privacy_mode import InstancePrivacyMode
from models.privacy_mode import PrivacyUserRestriction
from services.privacy_mode import PrivacyModeBlocked
from services.privacy_mode import assert_remote_allowed
from services.privacy_mode import effective_privacy_mode
from services.privacy_mode import is_remote_model
from services.privacy_mode import resolve_local_fallback_model
from services.privacy_mode import resolve_model_for_request
from services.privacy_mode import validate_instance_privacy_patch

import config


class PrivacyFakeStore:
    def __init__(self):
        self.app_settings: dict[str, str] = {}
        self.privacy_user: dict[str, str] = {}
        self.audit_rows: list[dict] = []

    def ping(self) -> bool:
        return True

    def execute(self, query: str, params: tuple | None = None) -> None:
        q = " ".join(query.lower().split())
        params = params or ()
        if "insert into app_settings" in q or "on conflict" in q:
            self.app_settings[params[0]] = params[1]
        elif "delete from privacy_user_settings" in q:
            self.privacy_user.pop(params[0], None)
        elif "insert into privacy_user_settings" in q:
            self.privacy_user[params[0]] = params[1]
        elif "insert into audit_log" in q:
            self.audit_rows.append({"params": params})

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = " ".join(query.lower().split())
        params = params or ()
        if "from app_settings" in q:
            v = self.app_settings.get(params[0])
            return {"value": v} if v is not None else None
        if "from privacy_user_settings" in q:
            r = self.privacy_user.get(params[0])
            return {"restriction": r} if r else None
        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list:
        return []

    def close(self) -> None:
        pass

    @contextmanager
    def transaction(self):
        yield


_FAKE_MODELS = {
    "claude": {"adapter": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"},
    "chatgpt": {"adapter": "openai", "api_key_env": "OPENAI_API_KEY"},
    "llama": {"adapter": "openai", "base_url": "http://ollama:11434/v1"},
    "remote_no_key": {"adapter": "openai", "base_url": "http://remote-llm:8080/v1"},
}


@pytest.fixture
def privacy_store(monkeypatch):
    store = PrivacyFakeStore()
    monkeypatch.setitem(config._instances, "metadata_store", store)
    monkeypatch.setattr(config, "get_all_models_config", lambda: dict(_FAKE_MODELS))
    monkeypatch.setattr(
        config,
        "get_model_config",
        lambda name: dict(_FAKE_MODELS[name]),
    )
    monkeypatch.setattr(
        config,
        "is_local_model",
        lambda name: "ollama" in (_FAKE_MODELS.get(name, {}).get("base_url") or "").lower(),
    )
    monkeypatch.setattr("auth.auth_enabled", lambda: False)
    yield store


def test_absent_app_settings_defaults_local_only_fresh_install(privacy_store):
    pol = effective_privacy_mode("default")
    assert pol.effective == InstancePrivacyMode.LOCAL_ONLY
    assert pol.instance_mode == InstancePrivacyMode.LOCAL_ONLY


def test_instance_allow_cloud_user_inherit_allows_remote(privacy_store, monkeypatch):
    privacy_store.app_settings["privacy_mode"] = "allow_cloud"
    monkeypatch.setattr("auth.auth_enabled", lambda: True)
    pol = effective_privacy_mode("alice")
    assert pol.effective == InstancePrivacyMode.ALLOW_CLOUD
    assert_remote_allowed("claude", "alice")


def test_instance_local_only_blocks_remote_is_model_enabled(privacy_store):
    assert config.is_model_enabled("claude", _privacy_blocks_remote=True) is False
    assert config.is_model_enabled("llama", _privacy_blocks_remote=True) is True


def test_keyless_remote_base_url_blocked_under_local_only(privacy_store):
    assert is_remote_model("remote_no_key") is True
    with pytest.raises(PrivacyModeBlocked):
        assert_remote_allowed("remote_no_key", "default")


def test_user_local_only_restriction_when_instance_allow_cloud(privacy_store, monkeypatch):
    privacy_store.app_settings["privacy_mode"] = "allow_cloud"
    privacy_store.privacy_user["alice"] = "local_only"
    monkeypatch.setattr("auth.auth_enabled", lambda: True)
    pol = effective_privacy_mode("alice")
    assert pol.effective == InstancePrivacyMode.LOCAL_ONLY


def test_effective_privacy_mode_none_user_uses_instance_only_auth_on(privacy_store, monkeypatch):
    privacy_store.app_settings["privacy_mode"] = "allow_cloud"
    privacy_store.privacy_user["alice"] = "local_only"
    monkeypatch.setattr("auth.auth_enabled", lambda: True)
    pol = effective_privacy_mode(None)
    assert pol.effective == InstancePrivacyMode.ALLOW_CLOUD
    assert pol.user_restriction == PrivacyUserRestriction.INHERIT


def test_resolve_model_for_request_swaps_remote_to_local_on_chat_path(privacy_store):
    model, meta = resolve_model_for_request("claude", "default")
    assert model == "llama"
    assert meta is not None
    assert meta["fallback_applied"] is True
    assert meta["requested_model"] == "claude"


def test_get_llm_provider_raises_privacy_mode_blocked_before_cache(privacy_store):
    with patch("services.privacy_mode.record_privacy_block"):
        with pytest.raises(PrivacyModeBlocked):
            config.get_llm_provider("claude", user_id="default")


def test_resolve_local_fallback_prefers_default_model_when_local(privacy_store):
    privacy_store.app_settings["default_model"] = "llama"
    assert resolve_local_fallback_model("default") == "llama"


def test_record_privacy_block_writes_audit_row(privacy_store):
    from services.privacy_mode import record_privacy_block

    with patch("services.privacy_mode.write_audit") as mock_write:
        record_privacy_block(requested_model="claude", user_id="default")
        mock_write.assert_called_once()
        entry = mock_write.call_args[0][0]
        assert entry.action_name == "privacy_mode_block"
        assert entry.connector == "llm"
        assert entry.mode == "privacy_gate"


def test_auth_off_ignores_privacy_user_settings(privacy_store, monkeypatch):
    privacy_store.app_settings["privacy_mode"] = "allow_cloud"
    privacy_store.privacy_user["default"] = "local_only"
    monkeypatch.setattr("auth.auth_enabled", lambda: False)
    pol = effective_privacy_mode("default")
    assert pol.effective == InstancePrivacyMode.ALLOW_CLOUD


def test_post_migration_allow_cloud_seed_preserves_cloud_access(privacy_store, monkeypatch):
    """Simulates migration 043 seeding privacy_mode=allow_cloud on upgrade."""
    privacy_store.app_settings["privacy_mode"] = "allow_cloud"
    privacy_store.app_settings["OPENAI_API_KEY"] = "sk-test"
    monkeypatch.setattr("auth.auth_enabled", lambda: True)
    pol = effective_privacy_mode("alice")
    assert pol.effective == InstancePrivacyMode.ALLOW_CLOUD
    assert_remote_allowed("claude", "alice")


def test_fresh_install_stays_local_only_even_with_legacy_api_key_row(privacy_store):
    """Absent privacy_mode row defaults local-only (no cloud signals in migration)."""
    privacy_store.app_settings["OPENAI_API_KEY"] = "sk-test"
    pol = effective_privacy_mode("default")
    assert pol.effective == InstancePrivacyMode.LOCAL_ONLY


def test_validate_instance_privacy_patch_rejects_lock_with_allow_cloud():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        validate_instance_privacy_patch(
            current_mode=InstancePrivacyMode.LOCAL_ONLY,
            current_locked=False,
            new_mode=InstancePrivacyMode.ALLOW_CLOUD,
            new_locked=True,
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == "privacy_lock_requires_local_only"


def test_validate_instance_privacy_patch_rejects_allow_cloud_while_instance_locked():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        validate_instance_privacy_patch(
            current_mode=InstancePrivacyMode.LOCAL_ONLY,
            current_locked=True,
            new_mode=InstancePrivacyMode.ALLOW_CLOUD,
            new_locked=None,
        )
    assert exc.value.status_code == 400
    assert exc.value.detail in ("privacy_mode_locked", "privacy_lock_requires_local_only")


def test_privacy_gate_precedes_credential_resolution(privacy_store):
    with patch("services.privacy_mode.record_privacy_block"):
        with patch("services.llm_connector_map.effective_api_key") as mock_key:
            mock_key.side_effect = AssertionError(
                "credentials must not resolve before privacy gate"
            )
            with pytest.raises(PrivacyModeBlocked):
                config.get_llm_provider("claude", user_id="default")
            mock_key.assert_not_called()


def test_egress_guard_disabled_does_not_alter_privacy_gate(privacy_store, monkeypatch):
    monkeypatch.delenv("LUMOGIS_FF_EGRESS_GUARD", raising=False)
    with patch("services.privacy_mode.record_privacy_block"):
        with pytest.raises(PrivacyModeBlocked):
            config.get_llm_provider("claude", user_id="default")
