# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Auth off: an LLM key saved in Lumogis Web enables its model (LUM-889).

Lumogis Web saves a key with ``PUT /api/v1/me/connector-credentials/llm_<vendor>``,
which writes ``user_connector_credentials`` for the single auth-off user
``"default"``. The chat-time resolver (``llm_connector_map.effective_api_key``)
already walked those tiers, but ``config.is_model_enabled`` read only
``app_settings`` and the env under auth off. The key was resolvable and its
model still refused (``400 invalid_model``) and shown off (``no_credentials``).

As in ``test_credential_tier_runtime_wiring.py``, each test pairs the read side
(``/api/v1/models``, the chat gate) with the resolver, in one process, with no
restart between the save and the check.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from tests.test_credential_tiers_routes import _TEST_FERNET_KEY
from tests.test_credential_tiers_routes import _RoutesFakeStore

_MODELS = {
    "claude": {"adapter": "anthropic", "api_key_env": "ANTHROPIC_API_KEY", "label": "Claude"},
    "chatgpt": {
        "adapter": "openai",
        "optional": True,
        "api_key_env": "OPENAI_API_KEY",
        "label": "ChatGPT",
    },
    "qwen": {"adapter": "openai", "base_url": "http://ollama:11434/v1", "label": "Qwen"},
}

_DEV_USER = "default"


@pytest.fixture
def auth_off(monkeypatch, mock_vector_store, mock_embedder, mock_scheduler):
    """Auth off, no env keys, a real credential substrate on a fake store."""
    import services.privacy_mode as privacy_mode

    import config as _config
    from services import _credential_internals as ci

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", _TEST_FERNET_KEY)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    # Load-bearing: an env key would satisfy the legacy path and the tests
    # would pass for the wrong reason.
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setattr(_config, "get_all_models_config", lambda: dict(_MODELS))
    monkeypatch.setattr(_config, "_models_config", dict(_MODELS))
    # Cloud models allowed: the hint under test is the key, not privacy mode.
    monkeypatch.setattr(privacy_mode, "blocks_remote_models", lambda user_id: False)
    monkeypatch.setattr(
        privacy_mode, "resolve_model_for_request", lambda model, user_id: (model, None)
    )

    s = _RoutesFakeStore()
    s._vector_store = mock_vector_store
    s._embedder = mock_embedder
    s._scheduler = mock_scheduler
    _config._instances["metadata_store"] = s
    ci.reset_for_tests()
    yield s
    _config._instances.pop("metadata_store", None)
    ci.reset_for_tests()


@pytest.fixture
def client(auth_off):
    import main

    with TestClient(main.app) as c:
        yield c


def _reasons(client) -> dict:
    resp = client.get("/api/v1/models")
    assert resp.status_code == 200, resp.text
    return {m["id"]: (m["enabled"], m["disabled_reason"]) for m in resp.json()["models"]}


def _save_web_key(client, connector: str, key: str) -> None:
    resp = client.put(
        f"/api/v1/me/connector-credentials/{connector}",
        json={"payload": {"api_key": key}},
    )
    assert resp.status_code == 200, resp.text


def test_a_key_saved_in_lumogis_web_enables_its_model_without_a_restart(client):
    """The headline: save the key, and the picker and the chat gate both flip."""
    import config as _config
    from services import llm_connector_map

    assert _reasons(client)["claude"] == (False, "no_credentials")

    _save_web_key(client, "llm_anthropic", "sk-web-FAKE")

    # Resolvable (this already worked before LUM-889) ...
    assert llm_connector_map.effective_api_key(_DEV_USER, "ANTHROPIC_API_KEY") == "sk-web-FAKE"
    # ... and now offered, by both readers of the same rule.
    assert _reasons(client)["claude"] == (True, None)
    assert _config.is_model_enabled("claude", user_id=_DEV_USER) is True
    assert _config.model_disabled_reason("claude", user_id=_DEV_USER) is None
    # Other cloud models are not switched on by an unrelated key.
    assert _reasons(client)["chatgpt"] == (False, "optional_disabled")


def test_the_chat_gate_accepts_the_model_after_a_web_save(client, monkeypatch):
    """``POST /api/v1/chat/completions`` no longer answers ``400 invalid_model``.

    Everything after the gate is replaced by a sentinel: reaching it proves the
    gate let the model through, without driving a real LLM call.
    """
    import routes.api_v1.chat as api_chat

    def _past_the_gate(*_a, **_kw):
        raise HTTPException(status_code=418, detail="past_the_gate")

    monkeypatch.setattr(api_chat, "_resolve_scoped_injection", _past_the_gate)
    body = {"model": "claude", "messages": [{"role": "user", "content": "hi"}]}

    before = client.post("/api/v1/chat/completions", json=body)
    assert before.status_code == 400
    assert "invalid_model" in before.text

    _save_web_key(client, "llm_anthropic", "sk-web-FAKE")

    after = client.post("/api/v1/chat/completions", json=body)
    assert after.status_code == 418, after.text


def test_a_household_key_also_enables_the_model_under_auth_off(client):
    """The household tier is part of the same walk (LUM-24), auth mode notwithstanding."""
    from connectors.registry import LLM_ANTHROPIC

    from services import credential_tiers as ct
    from services import llm_connector_map

    ct.household_put_payload(LLM_ANTHROPIC, {"api_key": "sk-household-FAKE"}, actor="admin:root")

    assert llm_connector_map.effective_api_key(_DEV_USER, "ANTHROPIC_API_KEY") == (
        "sk-household-FAKE"
    )
    assert _reasons(client)["claude"] == (True, None)


def test_a_switched_off_web_key_keeps_the_model_off(client):
    """LUM-850 still holds under auth off: a disabled row is not a key."""
    import config as _config
    from services import connector_credentials as ccs

    _save_web_key(client, "llm_anthropic", "sk-web-FAKE")
    assert ccs.set_enabled(_DEV_USER, "llm_anthropic", enabled=False, actor="self") is not None

    assert _reasons(client)["claude"] == (False, "no_credentials")
    assert _config.is_model_enabled("claude", user_id=_DEV_USER) is False


def test_the_optional_toggle_still_gates_a_web_saved_key(auth_off):
    """A web key does not bypass the household ``optional_<model>`` switch."""
    import config as _config
    from services import connector_credentials as ccs

    ccs.put_payload(_DEV_USER, "llm_openai", {"api_key": "sk-web-FAKE"}, actor="self")

    assert _config.model_disabled_reason("chatgpt", user_id=_DEV_USER) == "optional_disabled"
    assert _config.is_model_enabled("chatgpt", user_id=_DEV_USER) is False


@pytest.mark.parametrize("hint", [None, "snapshot"])
@pytest.mark.parametrize("saved", [False, True])
def test_is_model_enabled_and_model_disabled_reason_agree(auth_off, saved, hint):
    """The LUM-881 invariant, on the auth-off tier path, with and without the hint."""
    import config as _config
    from services import connector_credentials as ccs
    from services import llm_connector_map

    if saved:
        ccs.put_payload(_DEV_USER, "llm_anthropic", {"api_key": "sk-web-FAKE"}, actor="self")
    kwargs: dict = {"user_id": _DEV_USER, "_privacy_blocks_remote": False}
    if hint == "snapshot":
        kwargs["_credentials_present"] = llm_connector_map.get_user_credentials_snapshot(_DEV_USER)

    enabled = _config.is_model_enabled("claude", **kwargs)
    reason = _config.model_disabled_reason("claude", **kwargs)

    assert enabled is saved
    assert enabled == (reason is None)
    assert reason == (None if saved else "no_credentials")
