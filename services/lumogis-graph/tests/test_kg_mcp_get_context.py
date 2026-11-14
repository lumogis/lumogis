# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Contracts for ``kg_mcp.server.graph_get_context`` (MCP tool surface)."""

from __future__ import annotations


def test_graph_get_context_max_fragments_zero_returns_empty(monkeypatch):
    """``max_fragments=0`` must not bypass capping (regression: ``if cap`` treated 0 as falsy)."""
    import graph.query as gq
    from kg_mcp.server import graph_get_context

    def _stub(*, query: str, context_fragments: list[str], user_id: str = "default", **_kw) -> None:
        context_fragments.extend([f"[Graph] line-{i}" for i in range(5)])

    monkeypatch.setattr(gq, "on_context_building", _stub)

    out = graph_get_context("q", user_id="u1", max_fragments=0)
    assert out == {"fragments": []}


def test_graph_get_context_max_fragments_negative_returns_empty(monkeypatch):
    import graph.query as gq
    from kg_mcp.server import graph_get_context

    def _stub(*, query: str, context_fragments: list[str], user_id: str = "default", **_kw) -> None:
        context_fragments.append("[Graph] only")

    monkeypatch.setattr(gq, "on_context_building", _stub)

    assert graph_get_context("q", max_fragments=-1) == {"fragments": []}


def test_graph_get_context_caps_positive(monkeypatch):
    import graph.query as gq
    from kg_mcp.server import graph_get_context

    def _stub(*, query: str, context_fragments: list[str], user_id: str = "default", **_kw) -> None:
        context_fragments.extend([f"[Graph] {i}" for i in range(10)])

    monkeypatch.setattr(gq, "on_context_building", _stub)

    body = graph_get_context("q", max_fragments=2)
    assert body["fragments"] == ["[Graph] 0", "[Graph] 1"]
