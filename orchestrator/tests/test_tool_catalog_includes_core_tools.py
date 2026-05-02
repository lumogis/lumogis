# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Every in-process :class:`ToolSpec` in ``services.tools.TOOL_SPECS`` appears
in the read-only :func:`services.unified_tools.build_tool_catalog` snapshot."""

from __future__ import annotations

import pytest
from services.capability_registry import CapabilityRegistry
from services.unified_tools import build_tool_catalog
from services.unified_tools import build_tool_catalog_for_user

from services import tools as services_tools

# Mirrors ``services.unified_tools._CORE_LLM_TOOL_NAMES`` (not exported; keep test stable).
_CORE_THREE = frozenset({"search_files", "read_file", "query_entity"})


def _isolated_build(**kwargs):
    r = kwargs.pop("capability_registry", None)
    if r is None:
        r = CapabilityRegistry()
    return build_tool_catalog(
        capability_registry=r,
        list_actions_fn=lambda: [],
        **kwargs,
    )


def test_catalog_contains_every_tool_spec_name() -> None:
    cat = _isolated_build()
    from_llm = {e.name for e in cat.entries if e.transport == "llm_loop"}
    expected = {s.name for s in services_tools.TOOL_SPECS}
    assert from_llm == expected, (from_llm, expected)


def test_deterministic_order_repeated_builds(monkeypatch: pytest.MonkeyPatch) -> None:
    a = _isolated_build()
    b = _isolated_build()
    assert a.entries == b.entries

    monkeypatch.setattr("permissions.get_connector_mode", lambda **kw: "ASK")
    c = build_tool_catalog_for_user(
        "alice",
        capability_registry=CapabilityRegistry(),
        list_actions_fn=lambda: [],
    )
    d = build_tool_catalog_for_user(
        "alice",
        capability_registry=CapabilityRegistry(),
        list_actions_fn=lambda: [],
    )
    assert c.entries == d.entries


def test_core_sources_for_builtin_three() -> None:
    # Snapshot only the three always-core tools; ignore plugin-proxy noise.
    core_specs = [s for s in services_tools.TOOL_SPECS if s.name in _CORE_THREE]
    if len(core_specs) < 3:
        pytest.skip("core tool slice unavailable in this environment")
    cat = _isolated_build(tool_specs=core_specs)
    for e in cat.entries:
        if e.name in _CORE_THREE:
            assert e.source == "core"
            assert e.origin_tier == "local"


def test_query_graph_spec_with_proxy_handler_is_source_proxy() -> None:
    """Same helper as `register_query_graph_proxy` build → ``source=proxy``."""
    three = [s for s in services_tools.TOOL_SPECS if s.name in _CORE_THREE]
    assert len(three) == 3
    graph_proxy = services_tools._build_query_graph_spec(services_tools._query_graph_proxy_handler)
    cat = _isolated_build(tool_specs=[*three, graph_proxy])
    qrows = [e for e in cat.entries if e.name == "query_graph" and e.transport == "llm_loop"]
    assert len(qrows) == 1
    assert qrows[0].source == "proxy"
    assert qrows[0].origin_tier == "capability_backed"
