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
    import services.privacy_mode as privacy_mode

    import config as _config

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
    monkeypatch.setattr(_config, "model_disabled_reason", lambda model, **_: None)
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


def test_models_include_disabled_reason_field(client, fake_models):
    """LUM-881: every descriptor carries ``disabled_reason`` (``None`` when enabled)."""
    body = client.get("/api/v1/models").json()
    assert body["models"], "fixture must list models"
    for m in body["models"]:
        assert m["enabled"] is True
        assert "disabled_reason" in m and m["disabled_reason"] is None


def test_models_report_privacy_mode_reason(client, fake_models, monkeypatch):
    """Plumbing only: the route copies the reason into the DTO and sorts.

    Whether the reason is *computed* correctly is proved by the unpatched
    ``test_models_route_end_to_end_reasons`` in ``test_privacy_mode.py``.
    """
    import config as _config

    monkeypatch.setattr(
        _config,
        "model_disabled_reason",
        lambda model, **_: "privacy_mode" if model == "claude" else None,
    )
    body = client.get("/api/v1/models").json()
    by_id = {m["id"]: m for m in body["models"]}
    assert by_id["claude"]["enabled"] is False
    assert by_id["claude"]["disabled_reason"] == "privacy_mode"
    assert by_id["ollama-mistral"]["enabled"] is True
    assert [m["id"] for m in body["models"]] == ["ollama-mistral", "claude"]


def test_models_compute_privacy_once_per_request(client, monkeypatch):
    """Both models are keyless, so the privacy check is reached for each — the
    policy lookup must still happen exactly once per request."""
    import services.privacy_mode as privacy_mode

    import config as _config

    cfgs = {
        "ollama-local": {"label": "Local", "base_url": "http://ollama:11434"},
        "remote-llm": {"label": "Remote", "base_url": "http://remote-llm:8080"},
    }
    calls: list[str | None] = []

    def counting(user_id):
        calls.append(user_id)
        return True

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(privacy_mode, "blocks_remote_models", counting)
    monkeypatch.setattr(_config, "get_all_models_config", lambda: dict(cfgs))
    monkeypatch.setattr(_config, "get_model_config", lambda m: dict(cfgs[m]))

    body = client.get("/api/v1/models").json()
    by_id = {m["id"]: m for m in body["models"]}
    assert by_id["ollama-local"]["disabled_reason"] is None
    assert by_id["remote-llm"]["disabled_reason"] == "privacy_mode"
    assert len(calls) == 1


def test_models_two_users_get_their_own_reason(client, monkeypatch):
    import services.llm_connector_map as llm_connector_map
    import services.privacy_mode as privacy_mode

    import config as _config

    cfgs = {
        "claude": {"adapter": "anthropic", "api_key_env": "ANTHROPIC_API_KEY", "label": "Claude"},
        "ollama-mistral": {"base_url": "http://ollama:11434", "label": "Mistral"},
    }
    snapshot_calls: list[str] = []

    def snapshot(user_id):
        snapshot_calls.append(user_id)
        return {"llm_anthropic"} if user_id == "alice-models" else set()

    monkeypatch.setenv("AUTH_SECRET", "test-models-reason-secret-do-not-use")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr(_config, "get_all_models_config", lambda: dict(cfgs))
    monkeypatch.setattr(_config, "get_model_config", lambda m: dict(cfgs[m]))
    monkeypatch.setattr(llm_connector_map, "get_user_credentials_snapshot", snapshot)
    monkeypatch.setattr(privacy_mode, "blocks_remote_models", lambda user_id: True)
    from auth import mint_access_token

    def reasons(user_id: str) -> dict:
        hdr = {"Authorization": f"Bearer {mint_access_token(user_id, 'user')}"}
        resp = client.get("/api/v1/models", headers=hdr)
        assert resp.status_code == 200
        return {m["id"]: m["disabled_reason"] for m in resp.json()["models"]}

    assert reasons("alice-models") == {"claude": "privacy_mode", "ollama-mistral": None}
    assert snapshot_calls == ["alice-models"]
    assert reasons("bob-models") == {"claude": "no_credentials", "ollama-mistral": None}
    assert snapshot_calls == ["alice-models", "bob-models"]


def test_models_drop_model_that_vanishes_mid_request(client, fake_models, monkeypatch):
    """A dynamic Ollama model deleted between listing and lookup is dropped, not a 500."""
    import config as _config

    monkeypatch.setattr(
        _config,
        "get_all_models_config",
        lambda: {"claude": {"label": "Claude", "provider": "anthropic"}, "ghost": {"label": "G"}},
    )

    def reason(model, **_):
        if model == "ghost":
            raise _config.UnknownModelError("Unknown model 'ghost'")
        return None

    monkeypatch.setattr(_config, "model_disabled_reason", reason)
    resp = client.get("/api/v1/models")
    assert resp.status_code == 200
    assert [m["id"] for m in resp.json()["models"]] == ["claude"]


def test_models_other_value_error_is_not_swallowed(fake_models, monkeypatch):
    """Only ``UnknownModelError`` is caught; any other ``ValueError`` still errors."""
    import main

    import config as _config

    def reason(model, **_):
        raise ValueError("boom")

    monkeypatch.setattr(_config, "model_disabled_reason", reason)
    with TestClient(main.app, raise_server_exceptions=False) as c:
        assert c.get("/api/v1/models").status_code == 500


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
