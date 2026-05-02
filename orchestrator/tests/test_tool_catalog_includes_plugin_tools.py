# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Plugin-like :class:`ToolSpec` rows are classified as ``source=plugin`` because
Core does not tag ToolSpec with provenance today (see ``tool-vocabulary.md``).
"""

from __future__ import annotations

import json

from models.tool_spec import ToolSpec
from services.capability_registry import CapabilityRegistry
from services.unified_tools import build_tool_catalog


def _fake_handler(input_: dict, *, user_id: str) -> str:
    return json.dumps({"ok": True, "user_id": user_id})


def test_plugin_like_tool_in_catalog() -> None:
    plugin_spec = ToolSpec(
        name="catalog_test_plugin_tool",
        connector="example-connector",
        action_type="catalog_test_plugin",
        is_write=False,
        definition={
            "name": "catalog_test_plugin_tool",
            "description": "Catalog test-only tool",
            "parameters": {"type": "object", "properties": {}},
        },
        handler=_fake_handler,
    )
    core_minimal = [
        ToolSpec(
            name="search_files",
            connector="filesystem-mcp",
            action_type="search_files",
            is_write=False,
            definition={"name": "search_files", "parameters": {"type": "object"}},
            handler=_fake_handler,
        )
    ]
    cat = build_tool_catalog(
        tool_specs=[*core_minimal, plugin_spec],
        mcp_tools=[],
        capability_registry=CapabilityRegistry(),
        list_actions_fn=lambda: [],
    )
    rows = [e for e in cat.entries if e.name == "catalog_test_plugin_tool"]
    assert len(rows) == 1
    assert rows[0].source == "plugin"
    assert rows[0].transport == "llm_loop"
    assert rows[0].connector == "example-connector"
