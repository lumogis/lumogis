# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""MCP tool annotations for the KG ``graph.*`` surface (LUM-290 / LUM-297).

The five query/read tools advertise ``readOnlyHint=True``; ``graph.backfill``
mutates the graph and advertises ``readOnlyHint=False``. None are destructive
(no irreversible data loss) and all operate on the local closed graph store,
so ``openWorldHint=False`` throughout.
"""

from __future__ import annotations

import pytest

pytest.importorskip("mcp", reason="mcp SDK not installed in this environment")

_READ_ONLY = {
    "graph.query_ego",
    "graph.query_path",
    "graph.query_mentions",
    "graph.get_context",
    "graph.health",
}
_WRITE = {"graph.backfill"}


def _tools_by_name():
    from kg_mcp.server import build_fastmcp

    server = build_fastmcp()
    assert server is not None, "FastMCP should build when the mcp SDK is present"
    return {t.name: t for t in server._tool_manager.list_tools()}


def test_every_kg_tool_carries_annotations():
    tools = _tools_by_name()
    assert set(tools) == _READ_ONLY | _WRITE
    for name, tool in tools.items():
        assert tool.annotations is not None, f"{name} is missing tool annotations"
        # Local graph store — never the open web.
        assert tool.annotations.openWorldHint is False, f"{name} openWorldHint must be False"
        # No graph tool is irreversibly destructive today.
        assert tool.annotations.destructiveHint is False, f"{name} destructiveHint must be False"


@pytest.mark.parametrize("name", sorted(_READ_ONLY))
def test_kg_read_tools_are_read_only(name):
    ann = _tools_by_name()[name].annotations
    assert ann.readOnlyHint is True, f"{name} must advertise readOnlyHint=True"
    assert ann.idempotentHint is True, f"{name} must advertise idempotentHint=True"


def test_graph_backfill_is_a_non_idempotent_write():
    ann = _tools_by_name()["graph.backfill"].annotations
    assert ann.readOnlyHint is False, "graph.backfill mutates the graph"
    assert ann.idempotentHint is False, "graph.backfill re-runs can produce further writes"
