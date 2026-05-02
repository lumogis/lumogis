# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""MCP-visible tool names vs LLM-loop tool names — intentional divergence (C10).

Core's chat loop uses ``services.tools.TOOL_SPECS`` (underscore names such as
``search_files``). The streamable MCP surface at ``/mcp/`` exposes a separate
hand-coded community set in ``mcp_server.MCP_TOOLS_FOR_MANIFEST`` (dotted names
such as ``memory.search``). They are different contracts on purpose (ADR 010
"what is NOT in scope" for duplicating the full LLM tool set on MCP). This
test prevents silent drift of that assumption: today the name intersection is
empty; if a future tool is intentionally shared across both, update this
assertion and document it.
"""

from __future__ import annotations

import mcp_server
from services.capability_registry import CapabilityRegistry
from services.unified_tools import build_tool_catalog

from services import tools as services_tools


def test_mcp_and_llm_tool_name_sets_and_docstring_contract() -> None:
    mcp_names = {t.name for t in mcp_server.MCP_TOOLS_FOR_MANIFEST}
    llm_names = {s.name for s in services_tools.TOOL_SPECS}

    # Documented in mcp_server module header: five MCP community tools, separate
    # from the LLM tool registry list.
    assert mcp_names == {
        "memory.search",
        "memory.get_recent",
        "entity.lookup",
        "entity.search",
        "context.build",
    }
    # Current Core design: no shared tool name between MCP manifest and
    # TOOL_SPECS; bridge work is Phase 3+ if that changes.
    assert mcp_names & llm_names == set()

    cat = build_tool_catalog(
        list_actions_fn=lambda: [],
        capability_registry=CapabilityRegistry(),
    )
    in_cat_mcp = {e.name for e in cat.entries if e.source == "mcp"}
    in_cat_llm = {e.name for e in cat.entries if e.transport == "llm_loop"}
    assert in_cat_mcp == mcp_names
    assert in_cat_llm == llm_names
