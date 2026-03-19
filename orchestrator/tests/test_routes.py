# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""Tests for route endpoints using a test client."""

from unittest.mock import patch

import main
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
@patch("routes.chat.ask", return_value="mock chat")
def test_chat_completions_returns_200(mock_ask, mock_cfg, mock_ctx):
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
@patch("routes.chat.ask_stream", side_effect=_mock_stream)
def test_chat_completions_stream(mock_stream, mock_cfg, mock_ctx):
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
