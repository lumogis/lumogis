# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Per-user LLM provider cache + invalidation tests.

Plan ``llm_provider_keys_per_user_migration`` Pass 2.5 / 2.7.

Pins the three correctness invariants of the per-user LLM cache:

1. **Cache key shape isolates per user** — ``llm:{user_id}:{model}``
   for cloud models and ``llm:_local:{model}`` for local models.
2. **Substrate change-listener invalidates only the target user's
   cloud entries** — a PUT/DELETE for alice MUST evict alice's
   ``llm:alice:*`` slots and leave bob's intact, with the local
   shared slot ``llm:_local:*`` untouched (no per-user secret).
3. **`AUTH_ENABLED=true` + cloud + ``user_id is None`` raises
   ``TypeError``** — programmer error caught loud, not a 4xx.
"""

from __future__ import annotations

import sys
import types

import pytest
from cryptography.fernet import Fernet

# Stub the OpenAI / Anthropic SDK modules BEFORE importing config so the
# adapter modules (which top-level-import them) load without the real
# packages installed in the test venv. The cache-key tests below never
# exercise the SDKs themselves — they only need the adapter classes to
# be constructible.
for _mod, _attrs in (
    ("openai", ("OpenAI",)),
    ("anthropic", ("Anthropic", "APIError", "APIStatusError", "APITimeoutError", "RateLimitError")),
):
    if _mod not in sys.modules:
        m = types.ModuleType(_mod)
        for _a in _attrs:
            setattr(m, _a, type(_a, (), {"__init__": lambda self, *a, **kw: None}))
        sys.modules[_mod] = m

from tests.test_connector_credentials_service import _FakeStore

import config
from services import connector_credentials as svc


class _StubAdapter:
    """Minimal LLMProvider stand-in — captures __init__ kwargs."""

    _init_calls: list[dict] = []

    def __init__(self, **kwargs):
        type(self)._init_calls.append(dict(kwargs))
        self.kwargs = kwargs

    def chat(self, *a, **kw):
        return None

    def chat_stream(self, *a, **kw):
        if False:
            yield None


_MODELS = {
    "claude": {
        "adapter": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    "chatgpt": {
        "adapter": "openai",
        "model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
        "optional": False,
    },
    "llama": {
        "adapter": "openai",
        "model": "llama3.2:3b",
        "base_url": "http://ollama:11434/v1",
    },
}


@pytest.fixture
def store(monkeypatch):
    k1 = Fernet.generate_key().decode()
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", k1)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    svc.reset_for_tests()
    config._reregister_listeners_for_tests()

    s = _FakeStore()
    config._instances["metadata_store"] = s
    for key in [k for k in list(config._instances) if k.startswith("llm:")]:
        del config._instances[key]

    monkeypatch.setattr(config, "_models_config", _MODELS)

    # Stub adapter modules so get_llm_provider can construct without the
    # real OpenAI / Anthropic SDKs. We inject a fake submodule with the
    # exact symbols config.py imports inline.
    _StubAdapter._init_calls = []

    fake_anthropic = types.ModuleType("adapters.anthropic_llm")
    fake_anthropic.AnthropicLLM = _StubAdapter
    monkeypatch.setitem(sys.modules, "adapters.anthropic_llm", fake_anthropic)

    fake_openai = types.ModuleType("adapters.openai_llm")
    fake_openai.OpenAILLM = _StubAdapter
    monkeypatch.setitem(sys.modules, "adapters.openai_llm", fake_openai)

    yield s

    config._instances.pop("metadata_store", None)
    for key in [k for k in list(config._instances) if k.startswith("llm:")]:
        del config._instances[key]
    svc.reset_for_tests()


# ---------------------------------------------------------------------------
# Cache-key shape: per-user under auth-on, _local for local models.
# ---------------------------------------------------------------------------


def test_get_llm_provider_two_users_two_distinct_cache_slots(store):
    """Two users with their own keys produce two distinct cache slots."""
    svc.put_payload("alice", "llm_openai", {"api_key": "sk-alice-FAKE"}, actor="self")
    svc.put_payload("bob", "llm_openai", {"api_key": "sk-bob-FAKE"}, actor="self")

    alice_llm = config.get_llm_provider("chatgpt", user_id="alice")
    bob_llm = config.get_llm_provider("chatgpt", user_id="bob")

    assert "llm:alice:chatgpt" in config._instances
    assert "llm:bob:chatgpt" in config._instances
    assert id(alice_llm) != id(bob_llm), "per-user cache leaked across users"


def test_get_llm_provider_local_model_shared_cache(store):
    """Local model resolves to one shared instance regardless of user_id."""
    a = config.get_llm_provider("llama", user_id="alice")
    b = config.get_llm_provider("llama", user_id="bob")

    assert "llm:_local:llama" in config._instances
    assert id(a) == id(b), "local model adapters must share one cache slot"


def test_get_llm_provider_cloud_no_user_under_auth_on_raises(store):
    """Programmer error: cloud model + auth-on + no user_id → TypeError."""
    with pytest.raises(TypeError, match="user_id"):
        config.get_llm_provider("chatgpt", user_id=None)


# ---------------------------------------------------------------------------
# Substrate listener evicts the target user's cloud cache.
# ---------------------------------------------------------------------------


def test_credential_put_evicts_only_target_user(store):
    svc.put_payload("alice", "llm_openai", {"api_key": "sk-alice-1"}, actor="self")
    svc.put_payload("bob", "llm_openai", {"api_key": "sk-bob-1"}, actor="self")

    config.get_llm_provider("chatgpt", user_id="alice")
    config.get_llm_provider("chatgpt", user_id="bob")
    config.get_llm_provider("llama", user_id="alice")

    assert "llm:alice:chatgpt" in config._instances
    assert "llm:bob:chatgpt" in config._instances
    assert "llm:_local:llama" in config._instances

    svc.put_payload("alice", "llm_openai", {"api_key": "sk-alice-2"}, actor="self")

    assert "llm:alice:chatgpt" not in config._instances, (
        "alice's cloud cache slot should have been evicted by listener"
    )
    assert "llm:bob:chatgpt" in config._instances, (
        "bob's cloud cache slot must NOT be evicted by alice's PUT"
    )
    assert "llm:_local:llama" in config._instances, (
        "local model cache slot holds no per-user secret; do not evict"
    )


def test_credential_delete_evicts_target_user(store):
    svc.put_payload("alice", "llm_openai", {"api_key": "sk-alice-1"}, actor="self")
    svc.put_payload("bob", "llm_openai", {"api_key": "sk-bob-1"}, actor="self")

    config.get_llm_provider("chatgpt", user_id="alice")
    config.get_llm_provider("chatgpt", user_id="bob")

    svc.delete_payload("alice", "llm_openai", actor="self")

    assert "llm:alice:chatgpt" not in config._instances
    assert "llm:bob:chatgpt" in config._instances


def test_non_llm_connector_change_does_not_touch_llm_cache(store):
    """A change to a non-llm connector (e.g. caldav) MUST NOT evict any LLM slot."""
    svc.put_payload("alice", "llm_openai", {"api_key": "sk-alice-1"}, actor="self")
    config.get_llm_provider("chatgpt", user_id="alice")
    assert "llm:alice:chatgpt" in config._instances

    svc.put_payload(
        "alice",
        "caldav",
        {"base_url": "https://example.com/dav/", "username": "a", "password": "b"},
        actor="self",
    )

    assert "llm:alice:chatgpt" in config._instances, (
        "non-llm connector change must not touch the LLM adapter cache"
    )


# ---------------------------------------------------------------------------
# Auth-off behaviour preserved.
# ---------------------------------------------------------------------------


def test_get_llm_provider_auth_off_uses_global_slot(store, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-FAKE")

    llm = config.get_llm_provider("chatgpt", user_id=None)
    assert "llm:_global:chatgpt" in config._instances
    assert id(config._instances["llm:_global:chatgpt"]) == id(llm)
