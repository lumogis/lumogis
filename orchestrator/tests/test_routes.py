"""Tests for route endpoints using a test client."""

from unittest.mock import patch

import main
from fastapi.testclient import TestClient


@patch("routes.chat.ask", return_value="mock answer")
def test_ask_returns_200(mock_ask):
    with TestClient(main.app) as client:
        resp = client.post("/ask", json={"text": "hello"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "mock answer"


@patch("routes.chat.ask", return_value="mock chat")
def test_chat_completions_returns_200(mock_ask):
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


@patch("routes.chat.ask", return_value="streamed")
def test_chat_completions_stream(mock_ask):
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
