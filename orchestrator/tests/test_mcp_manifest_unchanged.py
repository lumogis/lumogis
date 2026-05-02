# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Phase 3B: core MCP manifest stays independent of LLM tool-catalog flag (regression)."""

from __future__ import annotations

import mcp_server
from models.capability import CapabilityTransport


def test_build_core_manifest_tool_set_stable() -> None:
    m = mcp_server.build_core_manifest()
    mcp = mcp_server.MCP_TOOLS_FOR_MANIFEST
    assert m.transport is CapabilityTransport.MCP
    assert {t.name for t in m.tools} == {t.name for t in mcp}
    assert len(m.tools) == 5
    # Ensure stable ids for snapshot-like regression (bump if MCP surface intentionally changes)
    assert {t.name for t in mcp} == {
        "memory.search",
        "memory.get_recent",
        "entity.lookup",
        "entity.search",
        "context.build",
    }
