# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""CI gate: MCP session/entity summary schemas MUST advertise ``scope``.

The plan §6 (`personal_shared_system_memory_scopes`) requires that the
public MCP capability surface declares the new ``scope`` visibility
dimension so external Lumogis-ecosystem agents can:

  * filter results by scope (``personal``/``shared``/``system``);
  * render badges that reflect provenance ("from Sara", "household
    fact", "system fact");
  * audit cross-household reads.

This is an interop contract: silently dropping the ``scope`` field
would make every downstream MCP client revert to single-user
assumptions. We lock it down with a CI gate so future schema
edits cannot regress the household-sharing story.

Both schemas live in ``orchestrator/mcp_server.py`` as
``_SESSION_SUMMARY_SCHEMA`` and ``_ENTITY_SUMMARY_SCHEMA``.
"""

from __future__ import annotations


def test_session_summary_schema_advertises_scope() -> None:
    import mcp_server

    schema = mcp_server._SESSION_SUMMARY_SCHEMA
    assert "scope" in schema["properties"], (
        "_SESSION_SUMMARY_SCHEMA must declare a 'scope' property "
        "(plan §6 — MCP capability contract)."
    )
    scope_prop = schema["properties"]["scope"]
    assert scope_prop.get("type") == "string"
    assert set(scope_prop.get("enum", [])) == {"personal", "shared", "system"}, (
        "scope enum must be exactly {personal, shared, system}; the "
        "MCP contract has no other valid values."
    )
    assert "scope" in schema["required"], (
        "scope MUST be in 'required' so MCP clients can rely on it "
        "without optional-field handling."
    )


def test_entity_summary_schema_advertises_scope() -> None:
    import mcp_server

    schema = mcp_server._ENTITY_SUMMARY_SCHEMA
    assert "scope" in schema["properties"], (
        "_ENTITY_SUMMARY_SCHEMA must declare a 'scope' property "
        "(plan §6 — MCP capability contract)."
    )
    scope_prop = schema["properties"]["scope"]
    assert scope_prop.get("type") == "string"
    assert set(scope_prop.get("enum", [])) == {"personal", "shared", "system"}
    assert "scope" in schema["required"]


def test_capability_manifest_round_trips_with_scope() -> None:
    """End-to-end: manifest → JSON → manifest must preserve the scope field."""
    import json

    import mcp_server

    manifest = mcp_server.build_core_manifest()
    body = json.loads(manifest.model_dump_json())

    # Find memory.search tool output schema; assert it references the
    # session summary schema with scope baked in.
    tool_by_name = {t["name"]: t for t in body["tools"]}
    out_schema = tool_by_name["memory.search"]["output_schema"]
    item_schema = out_schema["properties"]["results"]["items"]
    assert "scope" in item_schema["properties"]
    assert "scope" in item_schema["required"]

    out_schema = tool_by_name["entity.search"]["output_schema"]
    item_schema = out_schema["properties"]["entities"]["items"]
    assert "scope" in item_schema["properties"]
    assert "scope" in item_schema["required"]
