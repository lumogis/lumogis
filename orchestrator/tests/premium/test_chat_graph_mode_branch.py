# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for the GRAPH_MODE=service branch in `routes/chat.py:_inject_context`.

The chat hot path normally fires `Event.CONTEXT_BUILDING` and lets the
in-process graph plugin append `[Graph]`-prefixed fragments. When
`GRAPH_MODE=service` the plugin self-disables and Core must instead make
a synchronous `/context` HTTP call to the lumogis-graph service. These
tests pin both behaviours so the four-line addition can't silently regress.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from events import Event
from routes import chat as chat_route

import config


@pytest.fixture
def stub_inject_dependencies(monkeypatch):
    """Stub the network/DB-touching helpers used by `_inject_context`.

    `_inject_context` calls `get_budget`, `allocate`, `retrieve_context`,
    and `truncate_messages`. We don't care about their internals; we just
    need them to return defaults so the function reaches the
    GRAPH_MODE=service branch.
    """
    monkeypatch.setattr("services.memory.retrieve_context", lambda *a, **kw: [])
    monkeypatch.setattr(chat_route, "truncate_messages", lambda h, **kw: h)
    monkeypatch.setattr(chat_route, "truncate_text", lambda text, budget: text or "")
    monkeypatch.setattr(chat_route, "get_budget", lambda model: 4096)
    monkeypatch.setattr(
        chat_route,
        "allocate",
        lambda total, ratios: {
            "session_context": 200,
            "plugin_context": 200,
            "entities": 200,
            "history": 1000,
            "documents": 200,
        },
    )


def test_inject_context_inprocess_does_not_call_get_context_sync(
    stub_inject_dependencies, monkeypatch
):
    monkeypatch.setenv("GRAPH_MODE", "inprocess")
    config.clear_graph_mode_env_cache()

    sentinel = {"called": False}

    def _spy(**kwargs):
        sentinel["called"] = True
        return ["[Graph] should not be appended in inprocess mode"]

    monkeypatch.setattr("services.graph_webhook_dispatcher.get_context_sync", _spy)

    chat_route._inject_context("ada", history=[], model="m", user_id="u")

    assert sentinel["called"] is False, (
        "/context HTTP call must NOT happen in inprocess mode "
        "— the in-process plugin handles CONTEXT_BUILDING locally"
    )


def test_inject_context_service_calls_get_context_sync_and_appends(
    stub_inject_dependencies, monkeypatch
):
    monkeypatch.setenv("GRAPH_MODE", "service")
    config.clear_graph_mode_env_cache()

    captured: dict = {}

    def _stub(**kwargs):
        captured.update(kwargs)
        return ["[Graph] Ada knew Babbage."]

    monkeypatch.setattr("services.graph_webhook_dispatcher.get_context_sync", _stub)

    messages = chat_route._inject_context(
        "what does the graph know about Ada?",
        history=[],
        model="m",
        user_id="user-42",
    )

    assert captured == {
        "query": "what does the graph know about Ada?",
        "user_id": "user-42",
        "max_fragments": min(config.get_context_entity_budget(), 20),
    }
    rendered = "".join(m.get("content", "") for m in messages)
    assert "[Graph] Ada knew Babbage." in rendered, (
        "/context fragments must appear in the assembled context block"
    )


def test_inject_context_service_swallows_kg_failure(stub_inject_dependencies, monkeypatch):
    """If `get_context_sync` returns [] (KG offline / timeout / error), the
    chat path must continue without graph fragments — not raise, not stall.
    """
    monkeypatch.setenv("GRAPH_MODE", "service")
    config.clear_graph_mode_env_cache()

    monkeypatch.setattr(
        "services.graph_webhook_dispatcher.get_context_sync",
        lambda **kw: [],
    )

    with patch.object(chat_route, "hooks") as mock_hooks:
        mock_hooks.fire = lambda *a, **kw: None
        chat_route._inject_context("ada", history=[], model="m", user_id="u")


def test_inject_context_disabled_does_not_call_get_context_sync(
    stub_inject_dependencies, monkeypatch
):
    monkeypatch.setenv("GRAPH_MODE", "disabled")
    config.clear_graph_mode_env_cache()

    called = False

    def _spy(**kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr("services.graph_webhook_dispatcher.get_context_sync", _spy)

    chat_route._inject_context("ada", history=[], model="m", user_id="u")

    assert called is False


def test_inject_context_passes_user_id_to_context_building_hook(
    stub_inject_dependencies, monkeypatch
):
    monkeypatch.setenv("GRAPH_MODE", "inprocess")
    config.clear_graph_mode_env_cache()

    captured: dict = {}

    def _fire(event, **kwargs):
        if event == Event.CONTEXT_BUILDING:
            captured.update(kwargs)

    monkeypatch.setattr(chat_route.hooks, "fire", _fire)
    chat_route._inject_context("hello", history=[], model="m", user_id="uid-99")
    assert captured.get("user_id") == "uid-99"
    assert captured.get("query") == "hello"


def test_pooled_budget_includes_entities_slot(monkeypatch):
    """Non-sanitiser path must sum session + plugin + entities for truncate_text."""
    monkeypatch.setenv("GRAPH_MODE", "inprocess")
    config.clear_graph_mode_env_cache()
    monkeypatch.setattr("services.memory.retrieve_context", lambda *a, **kw: [])
    monkeypatch.setattr(chat_route, "truncate_messages", lambda h, **kw: h)
    monkeypatch.setattr(chat_route, "get_budget", lambda model: 4096)
    cap: dict = {}

    def _cap_truncate(text, max_tokens):
        cap["max_tokens"] = max_tokens
        return text or ""

    monkeypatch.setattr(chat_route, "truncate_text", _cap_truncate)
    monkeypatch.setattr(
        chat_route,
        "allocate",
        lambda total, ratios: {
            "session_context": 100,
            "plugin_context": 50,
            "entities": 75,
            "history": 1000,
            "response": 100,
            "documents": 40,
        },
    )

    def fake_fire(_event, **kwargs):
        kwargs["context_fragments"].append("synthetic graph grounding line")

    monkeypatch.setattr(chat_route.hooks, "fire", fake_fire)
    monkeypatch.setattr(config, "is_injection_sanitiser_enabled", lambda: False)

    chat_route._inject_context("q", [], "m", "u")
    assert cap.get("max_tokens") == max(96, 100 + 50 + 75 + 40)
