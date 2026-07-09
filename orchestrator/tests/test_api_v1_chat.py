# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``/api/v1/chat/{completions,models}`` — DTO contract + invariants.

Focus: the *façade* contract (validation, error mapping, model listing).
The underlying ``loop.ask`` / ``loop.ask_stream`` are mocked so the
test does not exercise the LLM stack.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from models.context_injection import ContextInjectionResult


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def fake_models(monkeypatch):
    import config as _config
    import services.privacy_mode as privacy_mode

    monkeypatch.setattr(privacy_mode, "blocks_remote_models", lambda user_id: False)
    monkeypatch.setattr(
        privacy_mode, "resolve_model_for_request", lambda model, user_id: (model, None)
    )
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
        lambda model, *, user_id=None, _privacy_blocks_remote=False: True,
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
        lambda model, *, user_id=None, _privacy_blocks_remote=False: False,
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


def test_unscoped_chat_calls_build_injected_context(client, fake_models, monkeypatch):
    """Unscoped chat (no document_id) invokes build_injected_context for auto-RAG (LUM-504)."""
    import routes.api_v1.chat as v1_chat

    injected_messages = [
        {"role": "user", "content": "[context] some doc\n\nhi"},
    ]
    fake_injection = ContextInjectionResult(
        messages=injected_messages, citations=[], auto_rag_point_ids=set()
    )
    monkeypatch.setattr(v1_chat, "build_injected_context", lambda *a, **kw: fake_injection)

    captured = {}

    def _fake_ask(question, *, history, model, use_tools, user_id, auto_rag_point_ids, **kw):
        captured["history"] = history
        captured["auto_rag_point_ids"] = auto_rag_point_ids
        return "the answer"

    monkeypatch.setattr(v1_chat, "ask", _fake_ask)

    resp = client.post(
        "/api/v1/chat/completions",
        json={
            "model": "claude",
            "stream": False,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 200
    # injection.messages replaces raw history passed to ask
    assert captured["history"] == injected_messages
    # auto_rag_point_ids passed through (not None / empty set → None)
    assert captured["auto_rag_point_ids"] is None  # empty set → falsy → None


def test_unscoped_chat_falls_back_on_injection_error(client, fake_models, monkeypatch):
    """If build_injected_context raises, unscoped chat falls back to plain history (LUM-504)."""
    import routes.api_v1.chat as v1_chat

    def _boom(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(v1_chat, "build_injected_context", _boom)
    monkeypatch.setattr(v1_chat, "ask", lambda *a, **kw: "fallback answer")

    resp = client.post(
        "/api/v1/chat/completions",
        json={
            "model": "claude",
            "stream": False,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["message"]["content"] == "fallback answer"


def test_unscoped_chat_streams_injected_history_and_point_ids(client, fake_models, monkeypatch):
    """Streaming unscoped chat passes injected history + populated auto_rag_point_ids to
    ask_stream and emits no lumogis citations (LUM-504)."""
    import routes.api_v1.chat as v1_chat
    from models.stream import StreamEvent

    import config as _config

    injected_messages = [{"role": "user", "content": "[context] excerpt\n\nhi"}]

    def _inject(question, history, model, user_id, *, auto_rag_point_ids=None, **kw):
        # Mirror the real build_injected_context: populate the shared set in place.
        if auto_rag_point_ids is not None:
            auto_rag_point_ids.add("point-123")
        return ContextInjectionResult(
            messages=injected_messages, citations=[], auto_rag_point_ids=auto_rag_point_ids or set()
        )

    monkeypatch.setattr(v1_chat, "build_injected_context", _inject)
    monkeypatch.setattr(_config, "get_llm_provider", lambda *a, **kw: MagicMock())

    captured = {}

    def _events(question, *, history, model, use_tools, user_id, auto_rag_point_ids, **kw):
        captured["history"] = history
        captured["auto_rag_point_ids"] = auto_rag_point_ids
        yield StreamEvent(type="text", content="hi")

    monkeypatch.setattr(v1_chat, "ask_stream", _events)

    resp = client.post(
        "/api/v1/chat/completions",
        json={
            "model": "claude",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 200
    # Drain the stream so the generator body runs and captures kwargs.
    payloads = []
    for line in resp.text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            payloads.append(json.loads(line.removeprefix("data: ")))

    assert captured["history"] == injected_messages
    # Populated point-id set is passed through verbatim (not collapsed to None).
    assert captured["auto_rag_point_ids"] == {"point-123"}
    # Unscoped chat surfaces no lumogis context citations.
    assert all("lumogis" not in p or p["lumogis"] is None for p in payloads)


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
