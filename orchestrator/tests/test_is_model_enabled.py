# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for config.is_model_enabled."""

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

import config


def _fake_store(settings: dict):
    """Build a mock MetadataStore whose fetch_one reads from settings dict."""
    store = MagicMock()

    def fetch_one(query, params):
        key = params[0] if params else None
        if key == "privacy_mode":
            return {"value": "allow_cloud"}
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


# ---------------------------------------------------------------------------
# LUM-881 step 0 — characterization table
#
# Written and run green on the *unmodified* ``is_model_enabled`` BEFORE it was
# split into ``_model_blocked_by_privacy`` + ``_model_availability_reason``.
# The last column of every row is a hard-coded literal, never computed from
# either function, so this table (not the agreement test in
# ``TestModelDisabledReason``) is the proof that enforcement did not change.
# ---------------------------------------------------------------------------

_CHAR_MODELS = {
    "qwen": {"adapter": "openai", "base_url": "http://ollama:11434/v1"},
    "remote": {"adapter": "openai", "base_url": "http://remote-llm:8080/v1"},
    "claude": {"adapter": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"},
    "chatgpt": {"adapter": "openai", "optional": True, "api_key_env": "OPENAI_API_KEY"},
}


class _CharStore:
    """Minimal metadata store: ``app_settings`` + ``privacy_user_settings``."""

    def __init__(self, app_settings: dict, privacy_user: dict | None = None):
        self.app_settings = app_settings
        self.privacy_user = privacy_user or {}

    def fetch_one(self, query, params=None):
        q = " ".join(query.lower().split())
        params = params or ()
        if "from app_settings" in q:
            v = self.app_settings.get(params[0])
            return {"value": v} if v is not None else None
        if "from privacy_user_settings" in q:
            r = self.privacy_user.get(params[0])
            return {"restriction": r} if r else None
        return None


def _row(
    case_id,
    model,
    expected,
    *,
    hint=None,
    privacy="allow_cloud",
    auth=False,
    user=None,
    env=False,
    stored=False,
    toggle=None,
    snap=None,
    restrict=None,
):
    """One truth-table row; only the inputs that differ from the defaults are named.

    hint:     None (the real ``blocks_remote_models`` runs) | True | False
    privacy:  instance ``privacy_mode`` in the fake store
    auth:     AUTH_ENABLED
    env:      key present (env var under auth off; ``has_credential`` under auth on
              when no ``snap`` is given)
    stored:   ``app_settings["ANTHROPIC_API_KEY"]`` set
    toggle:   ``app_settings["optional_chatgpt"]``
    snap:     ``_credentials_present`` set, or None for "no hint"
    restrict: this user's ``privacy_user_settings`` row
    expected: hard-coded literal, never computed
    """
    return (
        case_id,
        model,
        hint,
        privacy,
        auth,
        user,
        env,
        stored,
        toggle,
        snap,
        restrict,
        expected,
    )


_CHAR_ROWS = [
    _row("unknown", "nope", False, hint=False),
    _row("local-hint-true", "qwen", True, hint=True),
    _row("local-hint-false", "qwen", True, hint=False),
    _row("local-real-local-only", "qwen", True, privacy="local_only"),
    _row("local-real-allow", "qwen", True),
    _row("local-auth-on-no-user", "qwen", True, hint=False, auth=True),
    _row("local-auth-on-user", "qwen", True, hint=True, auth=True, user="alice"),
    _row("remote-hint-true", "remote", False, hint=True),
    _row("remote-hint-false", "remote", True, hint=False),
    _row("remote-real-local-only", "remote", False, privacy="local_only"),
    _row("remote-real-allow", "remote", True),
    _row("remote-auth-on-no-user-hint-false", "remote", True, hint=False, auth=True),
    _row("claude-off-env-hint-false", "claude", True, hint=False, env=True),
    _row("claude-off-env-hint-true", "claude", False, hint=True, env=True),
    _row("claude-off-env-real-local-only", "claude", False, privacy="local_only", env=True),
    _row("claude-off-env-real-allow", "claude", True, env=True),
    _row("claude-off-nokey-hint-false", "claude", False, hint=False),
    _row("claude-off-nokey-hint-true", "claude", False, hint=True),
    _row("claude-off-stored-hint-false", "claude", True, hint=False, stored=True),
    _row("claude-off-stored-hint-true", "claude", False, hint=True, stored=True),
    _row(
        "claude-on-snapshot-hint-false",
        "claude",
        True,
        hint=False,
        auth=True,
        user="alice",
        snap={"llm_anthropic"},
    ),
    _row(
        "claude-on-snapshot-hint-true",
        "claude",
        False,
        hint=True,
        auth=True,
        user="alice",
        snap={"llm_anthropic"},
    ),
    _row(
        "claude-on-empty-snapshot", "claude", False, hint=False, auth=True, user="alice", snap=set()
    ),
    _row(
        "claude-on-wrong-snapshot",
        "claude",
        False,
        hint=False,
        auth=True,
        user="alice",
        snap={"llm_openai"},
    ),
    _row("claude-on-no-user", "claude", False, hint=False, auth=True, snap={"llm_anthropic"}),
    _row("claude-on-no-hint-cred-yes-allow", "claude", True, auth=True, user="alice", env=True),
    _row("claude-on-no-hint-cred-no-allow", "claude", False, auth=True, user="alice"),
    _row(
        "claude-on-no-hint-cred-yes-local-only",
        "claude",
        False,
        privacy="local_only",
        auth=True,
        user="alice",
        env=True,
    ),
    _row(
        "claude-on-user-restricted",
        "claude",
        False,
        auth=True,
        user="alice",
        env=True,
        restrict="local_only",
    ),
    _row(
        "claude-on-env-not-consulted",
        "claude",
        False,
        hint=False,
        auth=True,
        user="alice",
        env=True,
        stored=True,
        snap=set(),
    ),
    _row("chatgpt-off-toggle-off-key", "chatgpt", False, hint=False, env=True, toggle="false"),
    _row("chatgpt-off-toggle-missing-key", "chatgpt", False, hint=False, env=True),
    _row("chatgpt-off-toggle-on-key", "chatgpt", True, hint=False, env=True, toggle="true"),
    _row("chatgpt-off-toggle-on-nokey", "chatgpt", False, hint=False, toggle="true"),
    _row(
        "chatgpt-off-toggle-on-key-hint-true", "chatgpt", False, hint=True, env=True, toggle="true"
    ),
    _row(
        "chatgpt-off-toggle-on-key-real-local-only",
        "chatgpt",
        False,
        privacy="local_only",
        env=True,
        toggle="true",
    ),
    _row("chatgpt-off-toggle-on-key-real-allow", "chatgpt", True, env=True, toggle="true"),
    _row(
        "chatgpt-on-toggle-on-snapshot",
        "chatgpt",
        True,
        hint=False,
        auth=True,
        user="alice",
        toggle="true",
        snap={"llm_openai"},
    ),
    _row(
        "chatgpt-on-toggle-off-snapshot",
        "chatgpt",
        False,
        hint=False,
        auth=True,
        user="alice",
        toggle="false",
        snap={"llm_openai"},
    ),
    _row(
        "chatgpt-on-toggle-on-empty-snapshot",
        "chatgpt",
        False,
        hint=False,
        auth=True,
        user="alice",
        toggle="true",
        snap=set(),
    ),
    _row(
        "chatgpt-on-toggle-on-no-user",
        "chatgpt",
        False,
        hint=False,
        auth=True,
        toggle="true",
        snap={"llm_openai"},
    ),
    _row(
        "chatgpt-on-toggle-on-snapshot-hint-true",
        "chatgpt",
        False,
        hint=True,
        auth=True,
        user="alice",
        toggle="true",
        snap={"llm_openai"},
    ),
]


class TestIsModelEnabledCharacterization:
    @pytest.mark.parametrize(
        "case",
        _CHAR_ROWS,
        ids=[r[0] for r in _CHAR_ROWS],
    )
    def test_is_model_enabled_truth_table(self, case, monkeypatch):
        (
            _id,
            model,
            hint,
            instance_privacy,
            auth_on,
            user_id,
            env_key,
            stored_key,
            toggle,
            snapshot,
            user_restriction,
            expected,
        ) = case

        monkeypatch.setenv("AUTH_ENABLED", "true" if auth_on else "false")
        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        if env_key and not auth_on:
            monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
            monkeypatch.setenv("OPENAI_API_KEY", "sk-env")

        settings = {"privacy_mode": instance_privacy}
        if stored_key:
            settings["ANTHROPIC_API_KEY"] = "sk-stored"
        if toggle is not None:
            settings["optional_chatgpt"] = toggle
        privacy_user = {user_id: user_restriction} if user_restriction else {}
        store = _CharStore(settings, privacy_user)

        with (
            patch.object(config, "_models_config", _CHAR_MODELS),
            patch.object(config, "get_metadata_store", return_value=store),
            patch(
                "services.llm_connector_map.has_credential",
                return_value=bool(env_key),
            ),
        ):
            kwargs = {"user_id": user_id}
            if hint is not None:
                kwargs["_privacy_blocks_remote"] = hint
            if snapshot is not None:
                kwargs["_credentials_present"] = snapshot
            assert config.is_model_enabled(model, **kwargs) is expected


# ---------------------------------------------------------------------------
# LUM-881 — config.model_disabled_reason
# ---------------------------------------------------------------------------

_REASON_MODELS = {
    "qwen": {"adapter": "openai", "base_url": "http://ollama:11434/v1"},
    "remote": {"adapter": "openai", "base_url": "http://remote-llm:8080/v1"},
    "claude": {"adapter": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"},
    "chatgpt": {"adapter": "openai", "optional": True, "api_key_env": "OPENAI_API_KEY"},
}


@pytest.fixture
def reason_env(monkeypatch):
    """Auth off, no env keys, empty household settings, ``_REASON_MODELS`` as config."""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    settings: dict = {"privacy_mode": "allow_cloud"}
    store = _CharStore(settings)
    with (
        patch.object(config, "_models_config", _REASON_MODELS),
        patch.object(config, "get_metadata_store", return_value=store),
    ):
        yield settings


class TestModelDisabledReason:
    def test_reason_none_for_local_model(self, reason_env):
        assert config.model_disabled_reason("qwen", _privacy_blocks_remote=True) is None

    def test_reason_privacy_mode_when_key_present_and_blocked(self, reason_env, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
        assert config.model_disabled_reason("claude", _privacy_blocks_remote=True) == "privacy_mode"

    def test_reason_no_credentials_wins_over_privacy(self, reason_env):
        """The precedence decision: a keyless install is never told about privacy."""
        assert (
            config.model_disabled_reason("claude", _privacy_blocks_remote=True) == "no_credentials"
        )

    def test_reason_optional_disabled_wins_over_privacy(self, reason_env, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        reason_env["optional_chatgpt"] = "false"
        assert (
            config.model_disabled_reason("chatgpt", _privacy_blocks_remote=True)
            == "optional_disabled"
        )

    def test_reason_no_credentials_for_optional_toggled_without_key(self, reason_env):
        reason_env["optional_chatgpt"] = "true"
        assert (
            config.model_disabled_reason("chatgpt", _privacy_blocks_remote=False)
            == "no_credentials"
        )

    def test_reason_none_when_cloud_allowed_and_key_present(self, reason_env, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
        assert config.model_disabled_reason("claude", _privacy_blocks_remote=False) is None

    def test_reason_auth_on_uses_credentials_snapshot(self, reason_env, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        have = {"llm_anthropic"}
        assert (
            config.model_disabled_reason(
                "claude", user_id="alice", _credentials_present=have, _privacy_blocks_remote=False
            )
            is None
        )
        assert (
            config.model_disabled_reason(
                "claude", user_id="alice", _credentials_present=have, _privacy_blocks_remote=True
            )
            == "privacy_mode"
        )
        assert (
            config.model_disabled_reason(
                "claude",
                user_id="alice",
                _credentials_present=set(),
                _privacy_blocks_remote=True,
            )
            == "no_credentials"
        )

    def test_reason_auth_on_no_user_is_no_credentials(self, reason_env, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        assert (
            config.model_disabled_reason(
                "claude", user_id=None, _credentials_present={"llm_anthropic"}
            )
            == "no_credentials"
        )

    def test_model_disabled_reason_unknown_model_raises(self, reason_env):
        with pytest.raises(config.UnknownModelError):
            config.model_disabled_reason("nope")
        assert issubclass(config.UnknownModelError, ValueError)

    def test_reason_values_match_contract(self, reason_env, monkeypatch):
        import typing

        from models.api_v1 import ModelDisabledReason

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
        reason_env["optional_chatgpt"] = "false"
        seen = {
            config.model_disabled_reason("claude", _privacy_blocks_remote=True),
            config.model_disabled_reason("chatgpt", _privacy_blocks_remote=False),
            config.model_disabled_reason("remote", _privacy_blocks_remote=True),
        }
        monkeypatch.delenv("ANTHROPIC_API_KEY")
        seen.add(config.model_disabled_reason("claude", _privacy_blocks_remote=False))
        assert seen == set(typing.get_args(ModelDisabledReason))

    @pytest.mark.parametrize("model", ["qwen", "remote", "claude", "chatgpt"])
    @pytest.mark.parametrize("has_key", [True, False])
    @pytest.mark.parametrize("toggle", ["true", "false"])
    @pytest.mark.parametrize("blocks", [True, False])
    @pytest.mark.parametrize("auth_on", [True, False])
    def test_is_model_enabled_agrees_with_reason(
        self, reason_env, monkeypatch, model, has_key, toggle, blocks, auth_on
    ):
        """Guards the *new* function against the old. Cannot catch a branch flipped
        in a shared helper (both sides move together) — the characterization
        table does that."""
        monkeypatch.setenv("AUTH_ENABLED", "true" if auth_on else "false")
        reason_env["optional_chatgpt"] = toggle
        if has_key and not auth_on:
            monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
            monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        snapshot = {"llm_anthropic", "llm_openai"} if has_key else set()
        kwargs = dict(user_id="alice", _credentials_present=snapshot, _privacy_blocks_remote=blocks)
        assert config.is_model_enabled(model, **kwargs) == (
            config.model_disabled_reason(model, **kwargs) is None
        )


# --- LUM-877: the ``_cfg`` hint on model_disabled_reason ---------------------


def test_model_disabled_reason_cfg_hint_is_transparent(monkeypatch):
    """A precomputed ``_cfg`` changes nothing and skips the config lookup."""
    models = {
        "llama": {"adapter": "openai", "base_url": "http://ollama:11434/v1"},
        "claude": {"adapter": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"},
    }
    dyn = {"qwen": {"adapter": "openai", "base_url": "http://ollama:11434/v1", "tools": False}}
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr("services.llm_connector_map.has_credential", lambda uid, env: False)
    with (
        patch.object(config, "_models_config", models),
        patch.object(config, "_dynamic_ollama_models", lambda: dict(dyn)),
    ):
        for name in ("llama", "qwen", "claude"):
            plain = config.model_disabled_reason(name, user_id="u1", _privacy_blocks_remote=False)
            cfg = config.get_model_config(name)
            with patch.object(config, "get_model_config") as spy:
                hinted = config.model_disabled_reason(
                    name, user_id="u1", _privacy_blocks_remote=False, _cfg=cfg
                )
            assert hinted == plain, name
            spy.assert_not_called()
