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

import config
from __version__ import __version__

_log = logging.getLogger(__name__)

_DEFAULT_USER_ID = "default"


try:
    from mcp.server.fastmcp import FastMCP as _FastMCP
except ImportError:
    _FastMCP = None  # type: ignore[assignment]
    _log.warning(
        "mcp package not installed — KG /mcp surface disabled. "
        "Install `mcp>=1.10.0` to enable."
    )


# ---------------------------------------------------------------------------
# Tool implementations — thin wrappers over the existing `graph.query` API.
# Each accepts only JSON-serialisable args + returns JSON-serialisable
# output (FastMCP enforces this via its tool-arg Pydantic adapter).
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
            "user_id": user_id,
        }
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
            "user_id": user_id,
        }
    )
    return {"output": output}


def graph_query_mentions(
    entity: str, limit: int = 20, user_id: str = _DEFAULT_USER_ID
) -> dict:
    """Documents/sessions mentioning an entity."""
    from graph.query import query_graph_tool

    output = query_graph_tool(
        {
            "mode": "mentions",
            "entity": entity,
            "limit": limit,
            "user_id": user_id,
        }
    )
    return {"output": output}


def graph_get_context(
    query: str,
    user_id: str = _DEFAULT_USER_ID,
    max_fragments: int = 5,
) -> dict:
    """Build a `[Graph]` context fragment list for a query.

    Mirrors `routes/context.py` semantics so MCP clients get the same
    output a Core chat would. Fail-soft: an empty list is a valid result.
    """
    from graph.query import on_context_building

    fragments: list[str] = []
    try:
        on_context_building(query=query, context_fragments=fragments)
    except Exception:
        _log.exception("graph.get_context (mcp): on_context_building raised")
    return {"fragments": fragments[:max_fragments] if max_fragments else fragments}


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
    from routes.health import _check_falkordb, _check_postgres

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
            "Lumogis Graph Pro — knowledge-graph query, context, and "
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
    )(graph_query_ego)
    fresh.tool(
        name="graph.query_path",
        description="Shortest path between two entities, max depth K.",
    )(graph_query_path)
    fresh.tool(
        name="graph.query_mentions",
        description="Documents/sessions mentioning an entity.",
    )(graph_query_mentions)
    fresh.tool(
        name="graph.get_context",
        description="Build a `[Graph]` context fragment for a query.",
    )(graph_get_context)
    fresh.tool(
        name="graph.backfill",
        description="Trigger a partial graph backfill.",
    )(graph_backfill)
    fresh.tool(
        name="graph.health",
        description="Detailed graph health (version, backend booleans, queue depth).",
    )(graph_health)
    return fresh


# Module-level singleton — built once at import so callers (main.py,
# routes/capabilities.py if it ever needs to introspect) can check
# `mcp is None` to detect SDK absence without invoking the factory.
mcp: Any = build_fastmcp()
