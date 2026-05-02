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
            "history": 1000,
        },
    )


def test_inject_context_inprocess_does_not_call_get_context_sync(
    stub_inject_dependencies, monkeypatch
):
    monkeypatch.setenv("GRAPH_MODE", "inprocess")
    config.get_graph_mode.cache_clear()

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
    config.get_graph_mode.cache_clear()

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
        "max_fragments": 3,
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
    config.get_graph_mode.cache_clear()

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
    config.get_graph_mode.cache_clear()

    called = False

    def _spy(**kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr("services.graph_webhook_dispatcher.get_context_sync", _spy)

    chat_route._inject_context("ada", history=[], model="m", user_id="u")

    assert called is False
