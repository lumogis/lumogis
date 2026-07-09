# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Document-scoped chat on ``POST /api/v1/chat/completions`` (LUM-175)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from models.context_injection import ContextInjectionResult
from models.context_injection import DocumentCitation


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
        "get_model_config",
        lambda m: {"claude": {"tools": True}}.get(m, {}),
    )
    monkeypatch.setattr(
        _config,
        "is_model_enabled",
        lambda model, *, user_id=None, _privacy_blocks_remote=False: True,
    )
    monkeypatch.setattr(_config, "is_local_model", lambda m: False)


def _sample_injection(*, with_citations: bool = True) -> ContextInjectionResult:
    citations = []
    if with_citations:
        citations = [
            DocumentCitation(
                chunk_index=4,
                file_path="/data/lease.pdf",
                score=0.82,
                score_kind="rerank",
            )
        ]
    return ContextInjectionResult(
        messages=[{"role": "user", "content": "ctx"}, {"role": "user", "content": "hi"}],
        citations=citations,
        auto_rag_point_ids={"pt-1"},
    )


def test_api_v1_chat_scoped_injects_context(client, fake_models, monkeypatch):
    import routes.api_v1.chat as v1_chat

    calls: dict = {}

    def _inject(*a, **kw):
        calls["scoped"] = kw.get("scoped_file_path")
        return _sample_injection()

    monkeypatch.setattr(v1_chat, "resolve_document_file_path", lambda u, i: "/data/lease.pdf")
    monkeypatch.setattr(v1_chat, "build_injected_context", _inject)
    monkeypatch.setattr(v1_chat, "ask", lambda *a, **kw: "answer")

    resp = client.post(
        "/api/v1/chat/completions",
        json={
            "model": "claude",
            "stream": False,
            "document_id": 42,
            "messages": [{"role": "user", "content": "summarise"}],
        },
    )
    assert resp.status_code == 200
    assert calls["scoped"] == "/data/lease.pdf"
    body = resp.json()
    assert body["lumogis"]["context_citations"][0]["chunk_index"] == 4


def test_api_v1_chat_unscoped_calls_injection(client, fake_models, monkeypatch):
    """Unscoped chat calls build_injected_context for auto-RAG (LUM-504).

    No citation DTO returned.
    """
    import routes.api_v1.chat as v1_chat
    from models.context_injection import ContextInjectionResult

    called = {"inject": False}

    def _inject(*a, **kw):
        called["inject"] = True
        return ContextInjectionResult(messages=[], citations=[], auto_rag_point_ids=set())

    monkeypatch.setattr(v1_chat, "build_injected_context", _inject)
    monkeypatch.setattr(v1_chat, "ask", lambda *a, **kw: "answer")

    resp = client.post(
        "/api/v1/chat/completions",
        json={
            "model": "claude",
            "stream": False,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 200
    assert called["inject"] is True
    assert resp.json().get("lumogis") is None


def test_api_v1_chat_scoped_disables_tools(client, fake_models, monkeypatch):
    import routes.api_v1.chat as v1_chat

    captured: dict = {}

    def _ask(*a, **kw):
        captured["use_tools"] = kw.get("use_tools")
        return "ok"

    monkeypatch.setattr(v1_chat, "resolve_document_file_path", lambda u, i: "/doc.pdf")
    monkeypatch.setattr(v1_chat, "build_injected_context", lambda *a, **kw: _sample_injection())
    monkeypatch.setattr(v1_chat, "ask", _ask)

    resp = client.post(
        "/api/v1/chat/completions",
        json={
            "model": "claude",
            "stream": False,
            "document_id": 1,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 200
    assert captured["use_tools"] is False


def test_api_v1_chat_document_not_found(client, fake_models, monkeypatch):
    import routes.api_v1.chat as v1_chat
    from services.document_scope import DocumentNotFoundError

    def _missing(*a, **kw):
        raise DocumentNotFoundError(999)

    monkeypatch.setattr(v1_chat, "resolve_document_file_path", _missing)

    resp = client.post(
        "/api/v1/chat/completions",
        json={
            "model": "claude",
            "stream": False,
            "document_id": 999,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == {"error": "document_not_found"}


def test_api_v1_chat_document_context_unavailable(client, fake_models, monkeypatch):
    import routes.api_v1.chat as v1_chat

    monkeypatch.setattr(v1_chat, "resolve_document_file_path", lambda u, i: "/empty.pdf")
    monkeypatch.setattr(
        v1_chat,
        "build_injected_context",
        lambda *a, **kw: _sample_injection(with_citations=False),
    )

    resp = client.post(
        "/api/v1/chat/completions",
        json={
            "model": "claude",
            "stream": False,
            "document_id": 1,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "document_context_unavailable"


def test_api_v1_chat_invalid_document_id(client, fake_models):
    resp = client.post(
        "/api/v1/chat/completions",
        json={
            "model": "claude",
            "stream": False,
            "document_id": 0,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == {"error": "invalid_document_id"}


def test_stream_emits_lumogis_context_citations(client, fake_models, monkeypatch):
    import routes.api_v1.chat as v1_chat
    from models.stream import StreamEvent

    monkeypatch.setattr(v1_chat, "resolve_document_file_path", lambda u, i: "/doc.pdf")
    monkeypatch.setattr(v1_chat, "build_injected_context", lambda *a, **kw: _sample_injection())

    def _events(*a, **kw):
        yield StreamEvent(type="text", content="hi")

    monkeypatch.setattr(v1_chat, "ask_stream", _events)
    import config as _config

    monkeypatch.setattr(_config, "get_llm_provider", lambda *a, **kw: MagicMock())

    resp = client.post(
        "/api/v1/chat/completions",
        json={
            "model": "claude",
            "stream": True,
            "document_id": 1,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 200
    first_data = None
    for line in resp.text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            first_data = json.loads(line.removeprefix("data: "))
            break
    assert first_data is not None
    assert first_data["lumogis"]["context_citations"][0]["chunk_index"] == 4
