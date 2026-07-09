# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for egress guard allowlist + wrapper (LUM-553)."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest
from models.privacy_mode import InstancePrivacyMode
from services import egress_guard as eg
from services.privacy_mode import PrivacyModeBlocked

import config


class _PrivacyFakeStore:
    def __init__(self) -> None:
        self.app_settings: dict[str, str] = {}
        self.privacy_user: dict[str, str] = {}

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
}


@pytest.fixture
def egress_env(monkeypatch):
    store = _PrivacyFakeStore()
    monkeypatch.setitem(config._instances, "metadata_store", store)
    monkeypatch.setattr(config, "get_all_models_config", lambda: dict(_FAKE_MODELS))
    monkeypatch.setattr(config, "get_model_config", lambda name: dict(_FAKE_MODELS[name]))
    monkeypatch.setattr(
        config,
        "is_local_model",
        lambda name: "ollama" in (_FAKE_MODELS.get(name, {}).get("base_url") or "").lower(),
    )
    monkeypatch.setattr("auth.auth_enabled", lambda: False)
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    eg._dynamic_ollama_cache = None
    eg._dynamic_ollama_cached_at = 0.0
    yield store


def test_egress_guard_disabled_by_default(monkeypatch):
    monkeypatch.delenv("LUMOGIS_FF_EGRESS_GUARD", raising=False)
    assert eg.egress_guard_enabled() is False


def test_allowlist_includes_postgres_qdrant_ollama(egress_env, monkeypatch):
    monkeypatch.delenv("LUMOGIS_FF_EGRESS_GUARD", raising=False)
    hosts = eg.build_allowlist(user_id=None, for_ceiling=False)
    assert "postgres" in hosts
    assert "qdrant" in hosts
    assert "ollama" in hosts


def test_allowlist_excludes_cloud_when_local_only(egress_env):
    hosts = eg.build_allowlist(user_id="default", for_ceiling=False)
    assert "api.anthropic.com" not in hosts
    assert "api.openai.com" not in hosts


def test_allowlist_includes_cloud_when_allow_cloud(egress_env):
    egress_env.app_settings["privacy_mode"] = "allow_cloud"
    hosts = eg.build_allowlist(user_id=None, for_ceiling=False)
    assert "api.anthropic.com" in hosts
    assert "api.openai.com" in hosts


def test_wrap_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("LUMOGIS_FF_EGRESS_GUARD", raising=False)

    class _Inner:
        pass

    inner = _Inner()
    assert eg.wrap_llm_provider_egress(inner, user_id="alice") is inner


def test_dynamic_ollama_hosts_cached(egress_env, monkeypatch):
    calls = {"n": 0}

    def _fake_dynamic():
        calls["n"] += 1
        return {"dyn": {"base_url": "http://ollama:11434/v1"}}

    monkeypatch.setattr(config, "_dynamic_ollama_models", _fake_dynamic)
    eg._dynamic_ollama_cache = None
    eg._dynamic_ollama_cached_at = 0.0
    eg.build_allowlist(user_id=None, for_ceiling=False)
    eg.build_allowlist(user_id=None, for_ceiling=False)
    assert calls["n"] == 1


def test_egress_blocked_not_counted_as_circuit_failure(monkeypatch):
    from services import circuit_breaker as cb

    monkeypatch.setenv("LUMOGIS_LLM_CIRCUIT_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_LLM_CIRCUIT_MAX_FAILURES", "3")
    cb.reset_all()

    class _Inner:
        def chat(self, messages, tools=None, system=None, max_tokens=4096):
            raise eg.EgressBlockedError(host="evil.example")

        def chat_stream(self, messages, tools=None, system=None, max_tokens=4096):
            raise eg.EgressBlockedError(host="evil.example")
            yield  # pragma: no cover

    wrapped = cb.wrap_llm_provider(_Inner(), "u:m")
    breaker = cb.get_breaker("llm:u:m", max_failures=3)
    for _ in range(3):
        with pytest.raises(eg.EgressBlockedError):
            wrapped.chat([{"role": "user", "content": "hi"}])
    assert breaker.state == "closed"


def test_privacy_still_blocks_before_egress(egress_env, monkeypatch):
    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD", "true")
    with patch("services.privacy_mode.record_privacy_block"):
        with pytest.raises(PrivacyModeBlocked):
            config.get_llm_provider("claude", user_id="default")


def test_sdk_wrapped_egress_block_translated(egress_env, monkeypatch):
    tethered = pytest.importorskip("tethered")
    from openai import APIConnectionError
    from unittest.mock import Mock

    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD", "true")

    class _Inner:
        def chat(self, messages, tools=None, system=None, max_tokens=4096):
            blocked = tethered.EgressBlocked(host="evil.example", port=443)
            raise APIConnectionError(
                message="connection failed",
                request=Mock(),
            ) from blocked

        def chat_stream(self, messages, tools=None, system=None, max_tokens=4096):
            yield  # pragma: no cover

    wrapped = eg.wrap_llm_provider_egress(_Inner(), user_id="alice")
    with pytest.raises(eg.EgressBlockedError) as excinfo:
        wrapped.chat([{"role": "user", "content": "hi"}])
    assert excinfo.value.host == "evil.example"


def test_scope_disables_tethered_localhost_subnet_exemption(egress_env, monkeypatch):
    tethered = pytest.importorskip("tethered")
    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD", "true")

    captured: dict = {}
    real_scope = tethered.scope

    @contextmanager
    def _spy_scope(*args, **kwargs):
        captured.update(kwargs)
        with real_scope(*args, **kwargs):
            yield

    monkeypatch.setattr(tethered, "scope", _spy_scope)

    class _Inner:
        def chat(self, messages, tools=None, system=None, max_tokens=4096):
            return {"ok": True}

        def chat_stream(self, messages, tools=None, system=None, max_tokens=4096):
            yield {"ok": True}

    wrapped = eg.wrap_llm_provider_egress(_Inner(), user_id="alice")
    wrapped.chat([{"role": "user", "content": "hi"}])
    assert captured.get("allow_localhost") is False
