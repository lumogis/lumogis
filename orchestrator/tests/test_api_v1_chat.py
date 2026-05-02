# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``/api/v1/chat/{completions,models}`` — DTO contract + invariants.

Focus: the *façade* contract (validation, error mapping, model listing).
The underlying ``loop.ask`` / ``loop.ask_stream`` are mocked so the
test does not exercise the LLM stack.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _single_user_dev_auth(monkeypatch):
    """Route tests use ``TestClient`` without bearer tokens — force dev mode."""
    monkeypatch.setenv("AUTH_ENABLED", "false")


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def fake_models(monkeypatch):
    import config as _config

    monkeypatch.setattr(
        _config,
        "get_all_models_config",
        lambda: {
            "claude": {"label": "Claude", "provider": "anthropic"},
            "ollama-mistral": {"label": "Mistral", "base_url": "http://ollama:11434"},
        },
    )
    monkeypatch.setattr(
        _config,
        "get_model_config",
        lambda m: {"claude": {"tools": True}, "ollama-mistral": {}}.get(m, {}),
    )
    monkeypatch.setattr(
        _config,
        "is_model_enabled",
        lambda model, *, user_id=None: True,
    )
    monkeypatch.setattr(
        _config,
        "is_local_model",
        lambda m: m.startswith("ollama-"),
    )


def test_models_lists_descriptors(client, fake_models):
    resp = client.get("/api/v1/models")
    assert resp.status_code == 200
    body = resp.json()
    ids = sorted(m["id"] for m in body["models"])
    assert ids == ["claude", "ollama-mistral"]
    by_id = {m["id"]: m for m in body["models"]}
    assert by_id["claude"]["provider"] == "anthropic"
    assert by_id["ollama-mistral"]["provider"] == "ollama"
    assert by_id["ollama-mistral"]["is_local"] is True


def test_chat_rejects_empty_user_message(client, fake_models):
    resp = client.post(
        "/api/v1/chat/completions",
        json={
            "model": "claude",
            "stream": False,
            "messages": [{"role": "user", "content": "   "}],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "empty_message"


def test_chat_rejects_assistant_last_message(client, fake_models):
    resp = client.post(
        "/api/v1/chat/completions",
        json={
            "model": "claude",
            "stream": False,
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "last_message_must_be_user"


def test_chat_rejects_system_message_not_first(client, fake_models):
    resp = client.post(
        "/api/v1/chat/completions",
        json={
            "model": "claude",
            "stream": False,
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "system", "content": "be terse"},
                {"role": "user", "content": "again"},
            ],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "system_message_position"


def test_chat_invalid_model_returns_400(client, fake_models, monkeypatch):
    import config as _config

    monkeypatch.setattr(
        _config,
        "is_model_enabled",
        lambda model, *, user_id=None: False,
    )
    resp = client.post(
        "/api/v1/chat/completions",
        json={
            "model": "ghost",
            "stream": False,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"].startswith("invalid_model:")


def test_chat_streaming_rejects_empty_even_with_rc_stub(client, fake_models, monkeypatch):
    monkeypatch.setenv("LUMOGIS_RC_CHAT_STUB", "true")
    resp = client.post(
        "/api/v1/chat/completions",
        json={
            "model": "claude",
            "stream": True,
            "messages": [{"role": "user", "content": "\n"}],
        },
        headers={"Accept": "text/event-stream"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "empty_message"


def test_chat_non_streaming_returns_assistant_message(client, fake_models, monkeypatch):
    import routes.api_v1.chat as v1_chat

    monkeypatch.setattr(v1_chat, "ask", lambda *a, **kw: "the answer")

    resp = client.post(
        "/api/v1/chat/completions",
        json={
            "model": "claude",
            "stream": False,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["model"] == "claude"
    assert body["message"]["role"] == "assistant"
    assert body["message"]["content"] == "the answer"
    assert body["id"].startswith("chatcmpl-")


def test_chat_streaming_rc_stub_skips_llm(client, fake_models, monkeypatch):
    monkeypatch.setenv("LUMOGIS_RC_CHAT_STUB", "true")
    monkeypatch.setenv("LUMOGIS_RC_CHAT_STUB_REPLY", "ping-rc-stub")
    resp = client.post(
        "/api/v1/chat/completions",
        json={
            "model": "claude",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"Accept": "text/event-stream"},
    )
    assert resp.status_code == 200
    assert "ping-rc-stub" in resp.text


def test_chat_503_when_credential_unavailable(client, fake_models, monkeypatch):
    import routes.api_v1.chat as v1_chat
    from services.connector_credentials import CredentialUnavailable

    def _boom(*a, **kw):
        raise CredentialUnavailable("no key")

    monkeypatch.setattr(v1_chat, "ask", _boom)

    resp = client.post(
        "/api/v1/chat/completions",
        json={
            "model": "claude",
            "stream": False,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "llm_provider_key_missing"
