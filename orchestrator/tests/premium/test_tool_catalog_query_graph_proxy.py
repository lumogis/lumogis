# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tool catalog assertions for KG ``query_graph`` proxy specs (premium)."""

from __future__ import annotations

from services.capability_registry import CapabilityRegistry
from services.kg_premium_core import _build_query_graph_spec
from services.kg_premium_core import _query_graph_proxy_handler
from services.unified_tools import build_tool_catalog

from services import tools as services_tools

# Mirrors ``services.unified_tools._CORE_LLM_TOOL_NAMES``.
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


def test_query_graph_spec_with_proxy_handler_is_source_proxy() -> None:
    """Same helper as `register_query_graph_proxy` build → ``source=proxy``."""
    three = [s for s in services_tools.TOOL_SPECS if s.name in _CORE_THREE]
    assert len(three) == 3
    graph_proxy = _build_query_graph_spec(_query_graph_proxy_handler)
    cat = _isolated_build(tool_specs=[*three, graph_proxy])
    qrows = [e for e in cat.entries if e.name == "query_graph" and e.transport == "llm_loop"]
    assert len(qrows) == 1
    assert qrows[0].source == "proxy"
    assert qrows[0].origin_tier == "capability_backed"
