# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""FastMCP server exposing the six `graph.*` tools at /mcp.

Mounted by `main.py` at `/mcp` exactly like Core's `mcp_server.py`. The
mount uses the same single-shot `session_manager.run()` pattern (rebuild
on every lifespan startup) so TestClient and production behave identically.

Why expose graph tools twice (here AND via `routes/tools.py`)?

  - `routes/tools.py:POST /tools/query_graph` is the CORE-INTERNAL fast
    path. Core's `services/tools.py:register_query_graph_proxy` posts to
    that route directly; lower latency than going through the MCP envelope.
  - `/mcp` here is the EXTERNAL-CLIENT surface. Thunderbolt and other
    MCP-speaking agents reach the six `graph.*` tools without learning
    Core's bespoke `query_graph` ToolSpec format.

Both surfaces dispatch into the same underlying `graph.query` helpers —
no business logic lives in this module.

Graceful degradation:
  If the `mcp` package is not installed at import time (e.g. a slimmed-down
  test environment), `mcp = None` and `main.py` skips the mount entirely.
  This mirrors Core's `mcp_server.py` policy.
"""

from __future__ import annotations

import logging
from typing import Any

from __version__ import __version__

import config

_log = logging.getLogger(__name__)

_DEFAULT_USER_ID = "default"


try:
    from mcp.server.fastmcp import FastMCP as _FastMCP
    from mcp.types import ToolAnnotations as _ToolAnnotations
except ImportError:
    _FastMCP = None  # type: ignore[assignment]
    _ToolAnnotations = None  # type: ignore[assignment]
    _log.warning(
        "mcp package not installed — KG /mcp surface disabled. Install `mcp>=1.27.2,<2` to enable."
    )


def _read_only_annotations(title: str) -> Any:
    """MCP tool annotations for a read-only graph query tool (LUM-290 / LUM-297).

    The five ``graph.query_*`` / ``graph.get_context`` / ``graph.health``
    tools read the local FalkorDB-backed graph and never mutate it, so MCP
    clients can auto-approve them. ``openWorldHint`` is ``False``: the graph
    is the operator's own closed knowledge store, not an external world.
    Returns ``None`` when the SDK is unavailable (graceful degradation).
    """
    if _ToolAnnotations is None:
        return None
    return _ToolAnnotations(
        title=title,
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )


def _write_annotations(title: str) -> Any:
    """MCP tool annotations for a state-mutating graph operator tool.

    ``graph.backfill`` triggers a reconciliation pass that writes to the
    graph: ``readOnlyHint=False``. It is **not** destructive (additive
    reconciliation, no irreversible data loss) and **not** idempotent
    (re-running can produce further writes). ``openWorldHint`` is ``False``
    (local store). Returns ``None`` when the SDK is unavailable.
    """
    if _ToolAnnotations is None:
        return None
    return _ToolAnnotations(
        title=title,
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )


# ---------------------------------------------------------------------------
# Tool implementations — thin wrappers over the existing `graph.query` API.
# Each accepts only JSON-serialisable args + returns JSON-serialisable
# output (FastMCP enforces this via its tool-arg Pydantic adapter).
#
# NOTE (LUM-884 / AI-002 class): `user_id` below comes from the MCP client, not
# from an authenticated identity. It is passed to `query_graph_tool` as the
# keyword argument, but nothing here verifies it. Out of scope for LUM-884;
# tracked in LUM-883 Later (bind to an authenticated identity).
# ---------------------------------------------------------------------------


def graph_query_ego(entity: str, max_depth: int = 2, user_id: str = _DEFAULT_USER_ID) -> dict:
    """Return the n-hop ego subgraph around an entity (dispatches to query_graph_tool)."""
    from graph.query import query_graph_tool

    if max_depth > 4:
        max_depth = 4
    output = query_graph_tool(
        {
            "mode": "ego",
            "entity": entity,
            "max_depth": max_depth,
        },
        user_id=user_id,
    )
    return {"output": output}


def graph_query_path(
    from_entity: str,
    to_entity: str,
    max_depth: int = 4,
    user_id: str = _DEFAULT_USER_ID,
) -> dict:
    """Shortest path between two entities, max depth K (≤ 4)."""
    from graph.query import query_graph_tool

    if max_depth > 4:
        max_depth = 4
    output = query_graph_tool(
        {
            "mode": "path",
            "from": from_entity,
            "to": to_entity,
            "max_depth": max_depth,
        },
        user_id=user_id,
    )
    return {"output": output}


def graph_query_mentions(entity: str, limit: int = 20, user_id: str = _DEFAULT_USER_ID) -> dict:
    """Documents/sessions mentioning an entity."""
    from graph.query import query_graph_tool

    output = query_graph_tool(
        {
            "mode": "mentions",
            "entity": entity,
            "limit": limit,
        },
        user_id=user_id,
    )
    return {"output": output}


def graph_get_context(
    query: str,
    user_id: str = _DEFAULT_USER_ID,
    max_fragments: int | None = None,
) -> dict:
    """Build a `[Graph]` context fragment list for a query.

    Mirrors `routes/context.py` semantics so MCP clients get the same
    output a Core chat would. Fail-soft: an empty list is a valid result.

    Default ``max_fragments`` matches Core service mode:
    ``min(get_context_entity_budget(), 20)``.

    Non-positive ``max_fragments`` (e.g. ``0``) returns no fragments — the
    HTTP ``ContextRequest`` rejects those values, and treating them as
    uncapped here was a bug (``0`` is falsy in ``if cap`` guards).
    """
    from graph.query import on_context_building

    if max_fragments is not None and max_fragments < 1:
        return {"fragments": []}

    cap = (
        max_fragments if max_fragments is not None else min(config.get_context_entity_budget(), 20)
    )
    fragments: list[str] = []
    try:
        on_context_building(query=query, context_fragments=fragments, user_id=user_id)
    except Exception:
        _log.exception("graph.get_context (mcp): on_context_building raised")
    return {"fragments": fragments[:cap]}


def graph_backfill(limit_per_type: int | None = None) -> dict:
    """Trigger a one-shot graph reconciliation pass.

    Synchronous — blocks the MCP call until reconciliation finishes.
    External clients are expected to call this rarely (operator action),
    not on a hot path. The HTTP surface (`POST /graph/backfill`) is the
    preferred channel for non-MCP callers because it's 202-async.
    """
    from graph.reconcile import run_reconciliation

    if config.get_graph_store() is None:
        return {"status": "skipped", "reason": "graph store not configured"}
    result = run_reconciliation(limit_per_type=limit_per_type)
    return {"status": "ok", "totals": result.get("totals", {})}


def graph_health() -> dict:
    """Return version + backend booleans + queue depth.

    Identical shape to `GET /health` so MCP clients don't need to learn
    a second response format. We intentionally do NOT call into
    `routes/health.health()` directly — that would create a circular
    runtime import. Instead, replicate the small read.
    """
    import webhook_queue
    from routes.health import _check_falkordb
    from routes.health import _check_postgres

    return {
        "status": "ok",
        "version": __version__,
        "falkordb": _check_falkordb(),
        "postgres": _check_postgres(),
        "pending_webhook_tasks": webhook_queue.qsize(),
    }


# ---------------------------------------------------------------------------
# FastMCP factory + module-level singleton (same single-shot pattern as Core).
# ---------------------------------------------------------------------------


def build_fastmcp() -> Any:
    """Construct a fresh FastMCP server with all six graph.* tools.

    Returns the FastMCP instance. The caller is responsible for calling
    `.streamable_http_app()` on it (which lazily creates the session
    manager) and entering `mcp.session_manager.run()` to start the
    underlying anyio task group.
    """
    if _FastMCP is None:
        return None

    fresh = _FastMCP(
        name="lumogis-graph",
        instructions=(
            "Lumogis Graph — knowledge-graph query, context, and "
            "operator tools backed by FalkorDB. Per-user via the user_id "
            "argument; single-user-local default."
        ),
        stateless_http=True,
        json_response=True,
    )
    # Make the public path exactly /mcp when mounted at /mcp in main.py.
    # Without this override the Starlette sub-app keeps its default /mcp
    # internal route, producing /mcp/mcp and a 307→404 redirect chain.
    fresh.settings.streamable_http_path = "/"

    fresh.tool(
        name="graph.query_ego",
        description="Return the n-hop ego subgraph around an entity.",
        annotations=_read_only_annotations("Ego subgraph"),
    )(graph_query_ego)
    fresh.tool(
        name="graph.query_path",
        description="Shortest path between two entities, max depth K.",
        annotations=_read_only_annotations("Shortest path"),
    )(graph_query_path)
    fresh.tool(
        name="graph.query_mentions",
        description="Documents/sessions mentioning an entity.",
        annotations=_read_only_annotations("Entity mentions"),
    )(graph_query_mentions)
    fresh.tool(
        name="graph.get_context",
        description="Build a `[Graph]` context fragment for a query.",
        annotations=_read_only_annotations("Graph context"),
    )(graph_get_context)
    fresh.tool(
        name="graph.backfill",
        description="Trigger a partial graph backfill.",
        annotations=_write_annotations("Graph backfill"),
    )(graph_backfill)
    fresh.tool(
        name="graph.health",
        description="Detailed graph health (version, backend booleans, queue depth).",
        annotations=_read_only_annotations("Graph health"),
    )(graph_health)
    return fresh


# Module-level singleton — built once at import so callers (main.py,
# routes/capabilities.py if it ever needs to introspect) can check
# `mcp is None` to detect SDK absence without invoking the factory.
mcp: Any = build_fastmcp()
