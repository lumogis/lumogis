# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""MCP tool annotation matrix helpers (LUM-299)."""

from __future__ import annotations

READ_TOOLS = frozenset(
    {
        "memory.search",
        "memory.get_recent",
        "entity.lookup",
        "entity.search",
        "context.build",
        "recall",
    }
)

WRITE_TOOLS = frozenset(
    {
        "add_memory",
        "add_entity",
        "add_relation",
        "forget",
        "update_observation",
        "checkpoint",
    }
)

ALL_TOOLS = READ_TOOLS | WRITE_TOOLS


def assert_annotation_matrix(tools: list[dict]) -> None:
    by_name = {t["name"]: t for t in tools}
    missing = ALL_TOOLS - set(by_name)
    assert not missing, f"missing tools in manifest: {sorted(missing)}"
    for name in READ_TOOLS:
        ann = by_name[name].get("annotations") or {}
        assert ann.get("readOnlyHint") is True, f"{name} must have readOnlyHint=True"
    for name in WRITE_TOOLS:
        ann = by_name[name].get("annotations") or {}
        assert ann.get("readOnlyHint") is not True, f"{name} must not be read-only"
