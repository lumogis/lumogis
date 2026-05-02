# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for route endpoints using a test client."""

from unittest.mock import patch

import main
import routes.chat as chat_routes
from fastapi.testclient import TestClient
from models.stream import StreamEvent


@patch("routes.chat.ask", return_value="mock answer")
def test_ask_returns_200(mock_ask):
    with TestClient(main.app) as client:
        resp = client.post("/ask", json={"text": "hello"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "mock answer"


@patch("routes.chat._inject_context", side_effect=lambda q, h, m, u: h)
@patch("routes.chat.config.get_model_config", return_value={"tools": True})
@patch("routes.chat.config.is_model_enabled", return_value=True)
@patch("routes.chat.ask", return_value="mock chat")
def test_chat_completions_returns_200(mock_ask, mock_enabled, mock_cfg, mock_ctx):
    with TestClient(main.app) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "claude",
                "stream": False,
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["choices"][0]["message"]["content"] == "mock chat"


def _mock_stream(*args, **kwargs):
    yield StreamEvent(type="text", content="streamed")


@patch("routes.chat._inject_context", side_effect=lambda q, h, m, u: h)
@patch("routes.chat.config.get_model_config", return_value={"tools": True})
@patch("routes.chat.config.is_model_enabled", return_value=True)
@patch("routes.chat.config.get_llm_provider", return_value=None)
@patch("routes.chat.ask_stream", side_effect=_mock_stream)
def test_chat_completions_stream(mock_stream, mock_provider, mock_enabled, mock_cfg, mock_ctx):
    with TestClient(main.app) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "claude",
                "stream": True,
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    assert "streamed" in resp.text


@patch("routes.chat._inject_context", side_effect=lambda q, h, m, u: h)
@patch(
    "routes.chat.config.get_model_config",
    return_value={"tools": False, "base_url": "http://ollama:11434/v1"},
)
@patch("routes.chat.config.is_model_enabled", return_value=True)
@patch("routes.chat.config.get_llm_provider", return_value=None)
@patch("routes.chat.ask_stream", side_effect=_mock_stream)
def test_local_stream_includes_loading_note_only_first_time(
    mock_stream, mock_provider, mock_enabled, mock_cfg, mock_ctx
):
    chat_routes._local_model_loading_note_shown.clear()
    try:
        with TestClient(main.app) as client:
            first = client.post(
                "/v1/chat/completions",
                json={
                    "model": "llama",
                    "stream": True,
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
            second = client.post(
                "/v1/chat/completions",
                json={
                    "model": "llama",
                    "stream": True,
                    "messages": [{"role": "user", "content": "again"}],
                },
            )
        assert first.status_code == 200
        assert second.status_code == 200
        assert "first time may take" in first.text
        assert "first time may take" not in second.text
    finally:
        chat_routes._local_model_loading_note_shown.clear()


@patch("routes.chat.config.is_model_enabled", return_value=False)
def test_chat_completions_disabled_model_returns_404(mock_enabled):
    with TestClient(main.app) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "chatgpt",
                "stream": False,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 404
    assert "not available" in resp.json()["detail"]


@patch(
    "routes.chat.config.get_all_models_config",
    return_value={
        "claude": {"adapter": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"},
        "qwen": {"adapter": "openai", "base_url": "http://ollama:11434/v1"},
        "chatgpt": {"adapter": "openai", "optional": True, "api_key_env": "OPENAI_API_KEY"},
    },
)
@patch(
    "routes.chat.config.is_model_enabled",
    side_effect=lambda n, **kwargs: n in ("claude", "qwen"),
)
def test_list_models_returns_only_enabled(mock_enabled, mock_all):
    with TestClient(main.app) as client:
        resp = client.get("/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    names = [m["id"] for m in data["data"]]
    assert "claude" in names
    assert "qwen" in names
    assert "chatgpt" not in names
