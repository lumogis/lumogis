# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-884 — each kg_mcp ``graph_query_*`` wrapper passes ``user_id`` as the keyword.

``query_graph_tool(input_, *, user_id)`` takes the caller's identity as a
keyword-only argument and ignores any ``user_id`` inside ``input_``. The
``capture`` stand-in below is keyword-only too, so a wrapper that still puts
``user_id`` in the dict (the old positional-dict call) fails loudly.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def captured(monkeypatch) -> dict:
    import graph.query as gq

    seen: dict = {}

    def capture(spec, *, user_id):
        seen["spec"] = dict(spec)
        seen["user_id"] = user_id
        return "ok"

    monkeypatch.setattr(gq, "query_graph_tool", capture)
    return seen


def test_graph_query_ego_passes_user_id_keyword(captured):
    from kg_mcp.server import graph_query_ego

    out = graph_query_ego("Ada", max_depth=2, user_id="alice")
    assert out == {"output": "ok"}
    assert captured["user_id"] == "alice"
    assert "user_id" not in captured["spec"]
    assert captured["spec"]["mode"] == "ego"


def test_graph_query_path_passes_user_id_keyword(captured):
    from kg_mcp.server import graph_query_path

    graph_query_path("Ada", "Babbage", max_depth=3, user_id="alice")
    assert captured["user_id"] == "alice"
    assert "user_id" not in captured["spec"]
    assert captured["spec"]["mode"] == "path"


def test_graph_query_mentions_passes_user_id_keyword(captured):
    from kg_mcp.server import graph_query_mentions

    graph_query_mentions("Ada", limit=5, user_id="alice")
    assert captured["user_id"] == "alice"
    assert "user_id" not in captured["spec"]
    assert captured["spec"]["mode"] == "mentions"
