# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Chat route credential-error envelope + per-user /v1/models filtering.

Plan ``llm_provider_keys_per_user_migration`` Pass 2.8.

These tests pin the **route-layer** contract:

* ``/v1/chat/completions`` returns a top-level OpenAI-compatible
  ``{"error": {...}}`` envelope (NOT FastAPI's default ``{"detail": ...}``)
  for the two domain errors:
  - 424 ``connector_not_configured``
  - 503 ``credential_unavailable``
* The streaming pre-flight runs **before** ``StreamingResponse`` is
  constructed so the same JSON envelope is returned (NOT
  ``text/event-stream``). The pre-flight MUST exercise the route's
  ``config.get_llm_provider`` call (i.e. patch
  ``services.llm_connector_map.effective_api_key`` to raise — patching
  ``loop.ask_stream`` would let a regression slip past where the route
  forgot to pre-flight at all).
* ``/v1/models`` is per-user filtered under ``AUTH_ENABLED=true`` and
  unchanged under ``AUTH_ENABLED=false``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import main
from auth import UserContext
from services.connector_credentials import (
    ConnectorNotConfigured,
    CredentialUnavailable,
)


_ALICE = UserContext(user_id="alice", role="user", is_authenticated=True)


@pytest.fixture
def chat_client():
    """TestClient that bypasses real auth via ``get_user`` patch."""
    with patch("routes.chat.get_user", return_value=_ALICE):
        with TestClient(main.app) as c:
            yield c


# ---------------------------------------------------------------------------
# Non-streaming 424 / 503 envelopes.
# ---------------------------------------------------------------------------


@patch("routes.chat._inject_context", side_effect=lambda q, h, m, u: h)
@patch("routes.chat.config.get_model_config",
       return_value={"tools": False, "api_key_env": "OPENAI_API_KEY"})
@patch("routes.chat.config.is_model_enabled", return_value=True)
@patch("routes.chat.ask",
       side_effect=ConnectorNotConfigured("alice has no llm_openai row"))
def test_chat_completions_424_on_missing_credential(
    _ask, _enabled, _cfg, _ctx, chat_client
):
    resp = chat_client.post(
        "/v1/chat/completions",
        json={"model": "chatgpt", "stream": False,
              "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 424
    body = resp.json()
    assert "detail" not in body, (
        "chat route MUST return OpenAI-style top-level error, not FastAPI detail"
    )
    assert body["error"]["code"] == "connector_not_configured"
    assert body["error"]["model"] == "chatgpt"
    assert body["error"]["type"] == "invalid_request_error"
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


@patch("routes.chat._inject_context", side_effect=lambda q, h, m, u: h)
@patch("routes.chat.config.get_model_config",
       return_value={"tools": False, "api_key_env": "OPENAI_API_KEY"})
@patch("routes.chat.config.is_model_enabled", return_value=True)
@patch("routes.chat.ask",
       side_effect=CredentialUnavailable("Fernet decrypt failed"))
def test_chat_completions_503_on_decrypt_failure(
    _ask, _enabled, _cfg, _ctx, chat_client
):
    resp = chat_client.post(
        "/v1/chat/completions",
        json={"model": "chatgpt", "stream": False,
              "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 503
    body = resp.json()
    assert "detail" not in body
    assert body["error"]["code"] == "credential_unavailable"
    assert body["error"]["type"] == "server_error"
    assert body["error"]["model"] == "chatgpt"


# ---------------------------------------------------------------------------
# Streaming pre-flight: 424 returns JSON, not SSE.
# ---------------------------------------------------------------------------


def _raise_not_configured(*a, **kw):
    raise ConnectorNotConfigured("no row for alice/llm_openai")


@patch("routes.chat._inject_context", side_effect=lambda q, h, m, u: h)
@patch("routes.chat.config.get_model_config",
       return_value={"tools": False, "api_key_env": "OPENAI_API_KEY"})
@patch("routes.chat.config.is_model_enabled", return_value=True)
@patch("routes.chat.config.get_llm_provider", side_effect=_raise_not_configured)
@patch("routes.chat.ask_stream")
def test_chat_completions_424_streaming_returns_json_not_sse(
    mock_ask_stream, mock_get_provider, _enabled, _cfg, _ctx, chat_client
):
    """The pre-flight MUST be the path that yields the 424 — not ask_stream."""
    resp = chat_client.post(
        "/v1/chat/completions",
        json={"model": "chatgpt", "stream": True,
              "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 424
    assert resp.headers["content-type"].startswith("application/json"), (
        "credential pre-flight failure MUST NOT emit text/event-stream"
    )
    body = resp.json()
    assert body["error"]["code"] == "connector_not_configured"
    # Pre-flight ran via the route, not via ask_stream.
    assert mock_get_provider.call_count == 1
    assert mock_ask_stream.call_count == 0, (
        "ask_stream must NOT have been entered after a pre-flight failure"
    )


# ---------------------------------------------------------------------------
# /v1/models per-user filtering.
# ---------------------------------------------------------------------------


def test_v1_models_under_auth_off_unchanged(monkeypatch):
    monkeypatch.setattr("routes.chat.auth_enabled", lambda: False)
    fake_models = {
        "claude": {"adapter": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"},
        "llama":  {"adapter": "openai", "base_url": "http://ollama:11434/v1"},
    }
    monkeypatch.setattr("routes.chat.config.get_all_models_config",
                        lambda: fake_models)
    monkeypatch.setattr("routes.chat.config.is_model_enabled",
                        lambda name, **kw: True)
    with TestClient(main.app) as c:
        resp = c.get("/v1/models")
    assert resp.status_code == 200
    ids = {m["id"] for m in resp.json()["data"]}
    assert ids == {"claude", "llama"}


def test_v1_models_per_user_filtering_under_auth_on(monkeypatch, chat_client):
    """Alice has llm_anthropic only → claude in, chatgpt out, llama in."""
    monkeypatch.setattr("routes.chat.auth_enabled", lambda: True)
    fake_models = {
        "claude":  {"adapter": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"},
        "chatgpt": {"adapter": "openai", "api_key_env": "OPENAI_API_KEY"},
        "llama":   {"adapter": "openai", "base_url": "http://ollama:11434/v1"},
    }
    monkeypatch.setattr("routes.chat.config.get_all_models_config",
                        lambda: fake_models)
    monkeypatch.setattr("routes.chat.get_user_credentials_snapshot",
                        lambda uid: {"llm_anthropic"})

    def _is_enabled(name, *, user_id=None, _credentials_present=None):
        cfg = fake_models[name]
        env = cfg.get("api_key_env")
        if not env:
            return True
        from services.llm_connector_map import connector_for_api_key_env
        connector = connector_for_api_key_env(env)
        return connector is not None and (
            _credentials_present is not None and connector in _credentials_present
        )

    monkeypatch.setattr("routes.chat.config.is_model_enabled", _is_enabled)
    resp = chat_client.get("/v1/models")
    assert resp.status_code == 200
    ids = {m["id"] for m in resp.json()["data"]}
    assert ids == {"claude", "llama"}, (
        f"alice has only llm_anthropic; expected claude+llama, got {ids}"
    )
