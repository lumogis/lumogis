# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for scoped build_injected_context (LUM-175)."""

from __future__ import annotations

import pytest
from events import Event
from models.memory import DocumentContextHit
from routes import chat as chat_route

import config


@pytest.fixture
def stub_scoped_dependencies(monkeypatch):
    monkeypatch.setattr(chat_route, "truncate_messages", lambda h, **kw: h)
    monkeypatch.setattr(chat_route, "get_budget", lambda model: 4096)
    monkeypatch.setattr(
        chat_route,
        "allocate",
        lambda total, ratios: {
            "system": 400,
            "session_context": 0,
            "entities": 0,
            "plugin_context": 0,
            "history": 1400,
            "documents": 1600,
            "response": 600,
        },
    )


def test_build_injected_context_scoped_skips_session_and_graph(
    stub_scoped_dependencies, monkeypatch
):
    session_called = {"v": False}
    graph_called = {"v": False}

    def _session(*a, **kw):
        session_called["v"] = True
        return []

    def _graph(**kw):
        graph_called["v"] = True
        return []

    monkeypatch.setattr("services.memory.retrieve_context", _session)
    monkeypatch.setattr("services.graph_webhook_dispatcher.get_context_sync", _graph)

    hit = DocumentContextHit(
        point_id="p1",
        file_path="/doc.pdf",
        chunk_text="chunk body",
        score=0.9,
        score_kind="rerank",
    )
    monkeypatch.setattr(
        "services.auto_rag.retrieve_document_context",
        lambda *a, **kw: [hit] if kw.get("scoped") else [],
    )
    monkeypatch.setattr(config, "is_injection_sanitiser_enabled", lambda: False)
    monkeypatch.setattr(config, "get_memory_hint_enabled", lambda: False)

    result = chat_route.build_injected_context(
        "q",
        [],
        "m",
        "u",
        scoped_file_path="/doc.pdf",
    )

    assert session_called["v"] is False
    assert graph_called["v"] is False
    assert len(result.citations) == 1
    assert result.citations[0].chunk_index is None


def test_build_injected_context_context_building_hook_unchanged(
    stub_scoped_dependencies, monkeypatch
):
    hit = DocumentContextHit(
        point_id="p1",
        file_path="/doc.pdf",
        chunk_text="chunk body",
        score=0.9,
        score_kind="rerank",
        chunk_index=7,
    )
    monkeypatch.setattr(
        "services.auto_rag.retrieve_document_context",
        lambda *a, **kw: [hit],
    )
    monkeypatch.setattr(config, "is_injection_sanitiser_enabled", lambda: False)
    monkeypatch.setattr(config, "get_memory_hint_enabled", lambda: False)

    captured: dict = {}

    def _subscriber(query, context_fragments, user_id):
        captured["ok"] = True

    import hooks

    hooks.register(Event.CONTEXT_BUILDING, _subscriber)
    try:
        chat_route.build_injected_context("hello", [], "m", "u1", scoped_file_path="/d.pdf")
        assert captured.get("ok") is True
    finally:
        hooks._listeners[Event.CONTEXT_BUILDING].remove(_subscriber)
