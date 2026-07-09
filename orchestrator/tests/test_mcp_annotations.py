# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""MCP tool annotations + spec-compliance posture for the Core surface.

LUM-290 / LUM-297: every registered MCP tool must advertise the four
annotation hints so clients (Cursor, Claude Desktop) can auto-approve safe
read-only calls instead of prompting on every invocation. All five Core
community tools are read-only, side-effect-free reads of the operator's own
closed memory/entity store — hence ``openWorldHint=False``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("mcp", reason="mcp SDK not installed in this environment")


# Expected read-only community tools.
_EXPECTED_READ_ONLY = {
    "memory.search",
    "memory.get_recent",
    "entity.lookup",
    "entity.search",
    "context.build",
}

_EXPECTED_WRITE = {
    "add_memory",
    "add_entity",
    "add_relation",
    "forget",
    "update_observation",
    "checkpoint",
}


def _tools_by_name():
    import mcp_server

    server = mcp_server.build_fastmcp()
    assert server is not None, "FastMCP should build when the mcp SDK is present"
    return {t.name: t for t in server._tool_manager.list_tools()}


def test_every_core_tool_carries_annotations():
    tools = _tools_by_name()
    assert _EXPECTED_READ_ONLY | _EXPECTED_WRITE <= set(tools)
    for name in _EXPECTED_READ_ONLY | _EXPECTED_WRITE:
        assert tools[name].annotations is not None, f"{name} is missing tool annotations"


@pytest.mark.parametrize("name", sorted(_EXPECTED_READ_ONLY))
def test_core_read_tools_are_annotated_read_only(name):
    tool = _tools_by_name()[name]
    ann = tool.annotations
    assert ann.readOnlyHint is True, f"{name} must advertise readOnlyHint=True"
    assert ann.destructiveHint is False, f"{name} must advertise destructiveHint=False"
    assert ann.idempotentHint is True, f"{name} must advertise idempotentHint=True"
    # Local closed-world memory/entity store — not the open web.
    assert ann.openWorldHint is False, f"{name} must advertise openWorldHint=False"


def test_core_tools_have_human_titles():
    tools = _tools_by_name()
    for name in _EXPECTED_READ_ONLY | _EXPECTED_WRITE:
        assert tools[name].annotations.title, f"{name} should carry a human-readable title"


@pytest.mark.parametrize("name", sorted(_EXPECTED_WRITE))
def test_core_write_tools_are_annotated_non_destructive(name):
    tool = _tools_by_name()[name]
    ann = tool.annotations
    assert ann.readOnlyHint is False, f"{name} must advertise readOnlyHint=False"
    assert ann.destructiveHint is False, f"{name} must advertise destructiveHint=False"
    assert ann.openWorldHint is False, f"{name} must advertise openWorldHint=False"


def test_advertised_protocol_version_is_at_least_2025_06_18():
    """Spec-compliance guard (LUM-290/297 acceptance criterion).

    The MCP SDK negotiates the handshake ``protocolVersion`` and the
    JSON-RPC error codes (-32602 / -32601). We require the SDK's advertised
    version to be at least 2025-06-18 (ISO date strings sort lexically),
    which is when tool annotations + the structured handshake stabilised.
    """
    from mcp.types import LATEST_PROTOCOL_VERSION

    assert LATEST_PROTOCOL_VERSION >= "2025-06-18", (
        f"MCP SDK advertises {LATEST_PROTOCOL_VERSION}, below the 2025-06-18 "
        "compliance floor; pin a newer `mcp` in requirements."
    )
