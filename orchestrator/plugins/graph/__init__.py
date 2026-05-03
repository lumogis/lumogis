# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Graph plugin: projects Lumogis hook events into FalkorDB.

Loaded automatically by plugins/__init__.py:load_plugins() at startup.

Implementation note
-------------------
The projection/query implementation lives in ``services/lumogis-graph/graph``.
Core keeps this ``plugins/graph`` package so ``load_plugins()`` can register
hooks and the ``query_graph`` ToolSpec when ``GRAPH_MODE=inprocess``.
See ``_ensure_lumogis_graph_sources_on_path()`` for ``sys.path`` wiring.

Behavior when FalkorDB is not configured (GRAPH_BACKEND != "falkordb"):
  - Hook handlers are registered but call config.get_graph_store() which returns None.
  - All handlers return immediately without touching the graph.
  - Core does not mount graph HTTP routes in-process; those live on ``services/lumogis-graph``.
  - Core ingest, search, and chat are fully unaffected.

Behavior when FalkorDB is configured but unreachable:
  - writer.py functions log the error and return.
  - graph_projected_at is NOT stamped (so reconciliation retries).
  - Core pipeline completes normally.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_log = logging.getLogger(__name__)


def _ensure_lumogis_graph_sources_on_path() -> None:
    """Append ``services/lumogis-graph`` so top-level ``graph.*`` resolves.

    Layouts:
      * Repo checkout: ``<repo>/orchestrator/plugins/graph/__init__.py``
      * Flat image (optional): ``<app>/plugins/graph/__init__.py`` with sibling
        ``<app>/services/lumogis-graph``
    """
    here = Path(__file__).resolve()
    roots = (
        here.parents[3] / "services" / "lumogis-graph",
        here.parents[2] / "services" / "lumogis-graph",
    )
    seen: set[Path] = set()
    for root in roots:
        root = root.resolve()
        if root in seen:
            continue
        seen.add(root)
        if root.is_dir():
            p = str(root)
            if p not in sys.path:
                sys.path.append(p)
            return
    _log.warning(
        "Graph plugin: lumogis-graph sources not found (checked %s) — "
        "``graph.*`` imports will fail until ``services/lumogis-graph`` is available.",
        ", ".join(str(r) for r in roots),
    )


_ensure_lumogis_graph_sources_on_path()

import hooks  # noqa: E402
from events import Event  # noqa: E402
from models.tool_spec import ToolSpec  # noqa: E402

import config  # noqa: E402


def _register_hook_handlers() -> None:
    from graph.writer import on_audio_transcribed
    from graph.writer import on_document_ingested
    from graph.writer import on_entity_created
    from graph.writer import on_entity_merged
    from graph.writer import on_note_captured
    from graph.writer import on_session_ended

    hooks.register(Event.DOCUMENT_INGESTED, on_document_ingested)
    hooks.register(Event.ENTITY_CREATED, on_entity_created)
    hooks.register(Event.SESSION_ENDED, on_session_ended)
    hooks.register(Event.NOTE_CAPTURED, on_note_captured)
    hooks.register(Event.AUDIO_TRANSCRIBED, on_audio_transcribed)
    hooks.register(Event.ENTITY_MERGED, on_entity_merged)
    _log.info("Graph plugin: M1 hook handlers registered")


def _register_query_handlers() -> None:
    """Register M3 read-path: CONTEXT_BUILDING hook + query_graph tool."""
    from graph.query import on_context_building
    from graph.query import query_graph_tool

    hooks.register(Event.CONTEXT_BUILDING, on_context_building)
    _log.info("Graph plugin: CONTEXT_BUILDING handler registered")

    spec = ToolSpec(
        name="query_graph",
        connector="lumogis-graph",
        action_type="query_graph",
        is_write=False,
        definition={
            "name": "query_graph",
            "description": (
                "Query the knowledge graph for connections, relationships, and sources. "
                "Use mode='ego' to find what entities are connected to a given entity. "
                "Use mode='path' to find how two entities are related. "
                "Use mode='mentions' to find which documents or sessions mention an entity. "
                "Returns structured results and a concise summary. "
                "If the graph is not configured, returns an explanatory message."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["ego", "path", "mentions"],
                        "description": (
                            "Query mode: 'ego' (neighborhood), 'path' (connection), "
                            "'mentions' (sources)."
                        ),
                    },
                    "entity": {
                        "type": "string",
                        "description": (
                            "Entity name to query (required for ego and mentions modes)."
                        ),
                    },
                    "from_entity": {
                        "type": "string",
                        "description": "Starting entity name (required for path mode).",
                    },
                    "to_entity": {
                        "type": "string",
                        "description": "Target entity name (required for path mode).",
                    },
                    "depth": {
                        "type": "integer",
                        "description": (
                            "Traversal depth for ego mode. "
                            "Currently fixed at 1 (direct neighbors only); "
                            "deeper traversal is not yet supported."
                        ),
                        "minimum": 1,
                        "maximum": 1,
                        "default": 1,
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Maximum path length for path mode (1–4, default 4).",
                        "minimum": 1,
                        "maximum": 4,
                        "default": 4,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results returned (1–20, default 10).",
                        "minimum": 1,
                        "maximum": 20,
                        "default": 10,
                    },
                },
                "required": ["mode"],
            },
        },
        handler=query_graph_tool,
    )
    hooks.fire(Event.TOOL_REGISTERED, spec)
    _log.info("Graph plugin: query_graph tool registered")


def _register_reconciliation_job() -> None:
    scheduler = config.get_scheduler()
    if scheduler is None:
        _log.warning("Graph plugin: scheduler not available — reconciliation job not registered")
        return
    try:
        from graph.reconcile import run_reconciliation

        scheduler.add_job(
            run_reconciliation,
            trigger="cron",
            hour=3,
            minute=0,
            id="graph_reconciliation",
            replace_existing=True,
        )
        _log.info("Graph plugin: reconciliation job scheduled (daily 03:00)")
    except ImportError:
        _log.warning(
            "Graph plugin: graph.reconcile.run_reconciliation not found — "
            "reconciliation job NOT scheduled."
        )
    except Exception:
        _log.exception("Graph plugin: failed to register reconciliation job")


# Mode guard: when GRAPH_MODE is anything other than `inprocess`, this
# plugin must NOT register hooks, schedule jobs, or expose a router. The
# Core lifespan in `service` mode dispatches every graph event over HTTP
# to the out-of-process `lumogis-graph` service; running both Core's
# in-process projection AND the service-side webhook receiver against the
# same Postgres + FalkorDB would double-write `graph_projected_at` and
# `edge_scores` on every ingest, silently corrupting graph state.
# `disabled` mode is the operator opting out entirely.
#
# `plugins/__init__.py:load_plugins()` already tolerates `router = None`
# (it filters with `if isinstance(router, APIRouter)`), so the loader is
# a no-op for this module when we set `router = None`.
_MODE = config.get_graph_mode()
if _MODE != "inprocess":
    _log.info(
        "Graph plugin: GRAPH_MODE=%s — plugin disabled (no hooks, no scheduler, no router)",
        _MODE,
    )
    router = None
else:
    _register_hook_handlers()
    _register_query_handlers()
    _register_reconciliation_job()

    _log.info(
        "Graph plugin loaded (GRAPH_BACKEND=%s)",
        __import__("os").environ.get("GRAPH_BACKEND", "none"),
    )

    # Graph HTTP (/graph/* backfill, viz, etc.) is implemented in ``services/lumogis-graph``
    # (graph/routes.py, graph/viz_routes.py). Core in-process mode only registers hooks,
    # the query_graph tool, and reconciliation — Core no longer mounts HTTP routers here.
    router = None
