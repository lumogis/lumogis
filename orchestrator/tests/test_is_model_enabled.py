# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for config.is_model_enabled."""

from unittest.mock import MagicMock
from unittest.mock import patch

import config


def _fake_store(settings: dict):
    """Build a mock MetadataStore whose fetch_one reads from settings dict."""
    store = MagicMock()

    def fetch_one(query, params):
        key = params[0] if params else None
        if key and key in settings:
            return {"value": settings[key]}
        return None

    store.fetch_one.side_effect = fetch_one
    return store


class TestIsModelEnabled:
    # --- Unknown model ---

    def test_unknown_model_returns_false(self):
        with patch.object(
            config,
            "_models_config",
            {"claude": {"adapter": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"}},
        ):
            assert config.is_model_enabled("nonexistent") is False

    # --- Local Ollama model (no api_key_env) ---

    def test_local_model_no_key_env_always_enabled(self):
        models = {"qwen": {"adapter": "openai", "base_url": "http://ollama:11434/v1"}}
        store = _fake_store({})
        with (
            patch.object(config, "_models_config", models),
            patch.object(config, "get_metadata_store", return_value=store),
        ):
            assert config.is_model_enabled("qwen") is True

    # --- Non-optional cloud model (claude) ---

    def test_non_optional_cloud_model_enabled_when_env_key_set(self, monkeypatch):
        models = {"claude": {"adapter": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"}}
        store = _fake_store({})
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        with (
            patch.object(config, "_models_config", models),
            patch.object(config, "get_metadata_store", return_value=store),
        ):
            assert config.is_model_enabled("claude") is True

    def test_non_optional_cloud_model_disabled_when_no_key(self, monkeypatch):
        models = {"claude": {"adapter": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"}}
        store = _fake_store({})
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with (
            patch.object(config, "_models_config", models),
            patch.object(config, "get_metadata_store", return_value=store),
        ):
            assert config.is_model_enabled("claude") is False

    def test_non_optional_cloud_model_enabled_from_stored_key(self, monkeypatch):
        models = {"claude": {"adapter": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"}}
        store = _fake_store({"ANTHROPIC_API_KEY": "sk-stored"})
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with (
            patch.object(config, "_models_config", models),
            patch.object(config, "get_metadata_store", return_value=store),
        ):
            assert config.is_model_enabled("claude") is True

    # --- Optional model ---

    def test_optional_model_disabled_by_default(self, monkeypatch):
        models = {
            "chatgpt": {"adapter": "openai", "optional": True, "api_key_env": "OPENAI_API_KEY"}
        }
        store = _fake_store({})
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        with (
            patch.object(config, "_models_config", models),
            patch.object(config, "get_metadata_store", return_value=store),
        ):
            assert config.is_model_enabled("chatgpt") is False

    def test_optional_model_enabled_when_toggled_and_key_present(self, monkeypatch):
        models = {
            "chatgpt": {"adapter": "openai", "optional": True, "api_key_env": "OPENAI_API_KEY"}
        }
        store = _fake_store({"optional_chatgpt": "true", "OPENAI_API_KEY": "sk-stored"})
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with (
            patch.object(config, "_models_config", models),
            patch.object(config, "get_metadata_store", return_value=store),
        ):
            assert config.is_model_enabled("chatgpt") is True

    def test_optional_model_toggled_but_no_key_returns_false(self, monkeypatch):
        models = {
            "chatgpt": {"adapter": "openai", "optional": True, "api_key_env": "OPENAI_API_KEY"}
        }
        store = _fake_store({"optional_chatgpt": "true"})
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with (
            patch.object(config, "_models_config", models),
            patch.object(config, "get_metadata_store", return_value=store),
        ):
            assert config.is_model_enabled("chatgpt") is False

    def test_optional_model_key_in_env_but_not_toggled(self, monkeypatch):
        models = {
            "chatgpt": {"adapter": "openai", "optional": True, "api_key_env": "OPENAI_API_KEY"}
        }
        store = _fake_store({})
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        with (
            patch.object(config, "_models_config", models),
            patch.object(config, "get_metadata_store", return_value=store),
        ):
            assert config.is_model_enabled("chatgpt") is False


class TestIsModelEnabledAuthOn:
    """Plan llm_provider_keys_per_user_migration Pass 2.5 — per-user gate."""

    def test_auth_on_no_user_id_returns_false_for_cloud_model(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        models = {"claude": {"adapter": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"}}
        with patch.object(config, "_models_config", models):
            assert config.is_model_enabled("claude") is False
            assert config.is_model_enabled("claude", user_id=None) is False

    def test_auth_on_no_row_returns_false(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        models = {"claude": {"adapter": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"}}
        with (
            patch.object(config, "_models_config", models),
            patch("services.llm_connector_map.has_credential", return_value=False),
        ):
            assert config.is_model_enabled("claude", user_id="alice") is False

    def test_auth_on_with_row_returns_true(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        models = {"claude": {"adapter": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"}}
        with (
            patch.object(config, "_models_config", models),
            patch("services.llm_connector_map.has_credential", return_value=True),
        ):
            assert config.is_model_enabled("claude", user_id="alice") is True

    def test_auth_on_optional_off_returns_false_even_with_row(self, monkeypatch):
        """Household optional toggle wins — set to off, per-user key irrelevant."""
        monkeypatch.setenv("AUTH_ENABLED", "true")
        models = {
            "chatgpt": {"adapter": "openai", "optional": True, "api_key_env": "OPENAI_API_KEY"}
        }
        store = _fake_store({"optional_chatgpt": "false"})
        with (
            patch.object(config, "_models_config", models),
            patch.object(config, "get_metadata_store", return_value=store),
            patch("services.llm_connector_map.has_credential", return_value=True),
        ):
            assert config.is_model_enabled("chatgpt", user_id="alice") is False

    def test_auth_on_optional_on_with_row_returns_true(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        models = {
            "chatgpt": {"adapter": "openai", "optional": True, "api_key_env": "OPENAI_API_KEY"}
        }
        store = _fake_store({"optional_chatgpt": "true"})
        with (
            patch.object(config, "_models_config", models),
            patch.object(config, "get_metadata_store", return_value=store),
            patch("services.llm_connector_map.has_credential", return_value=True),
        ):
            assert config.is_model_enabled("chatgpt", user_id="alice") is True

    def test_auth_on_credentials_present_hint_short_circuits(self, monkeypatch):
        """``_credentials_present`` set membership avoids the per-call DB read."""
        monkeypatch.setenv("AUTH_ENABLED", "true")
        models = {"claude": {"adapter": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"}}
        with (
            patch.object(config, "_models_config", models),
            patch(
                "services.llm_connector_map.has_credential",
                side_effect=AssertionError("must not call has_credential when hint provided"),
            ),
        ):
            assert (
                config.is_model_enabled(
                    "claude",
                    user_id="alice",
                    _credentials_present={"llm_anthropic"},
                )
                is True
            )
            assert (
                config.is_model_enabled(
                    "claude",
                    user_id="alice",
                    _credentials_present=set(),
                )
                is False
            )

    def test_auth_on_local_model_always_enabled(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        models = {"qwen": {"adapter": "openai", "base_url": "http://ollama:11434/v1"}}
        with patch.object(config, "_models_config", models):
            assert config.is_model_enabled("qwen", user_id=None) is True
            assert config.is_model_enabled("qwen", user_id="alice") is True
