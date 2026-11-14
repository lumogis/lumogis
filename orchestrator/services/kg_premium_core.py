# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Optional KG tooling: HTTP ``query_graph`` proxy ToolSpec (GRAPH_MODE=service).

Part of the optional knowledge-graph component (AGPL-3.0-only, ADR 229). Kept in
its own module so Core's :kmod:`services.capability_http` surface stays generic,
and Core degrades cleanly when the module is absent (LUM-242). The ``premium`` in
the filename is historical.
"""

from __future__ import annotations

import logging
from typing import Final

import hooks
from events import Event
from models.tool_spec import ToolSpec
from services.capability_http import post_capability_tool_invocation

import config

_log = logging.getLogger(__name__)

QUERY_GRAPH_MAX_DEPTH: Final[int] = 4
QUERY_GRAPH_PROXY_TIMEOUT_S: Final[float] = 2.5
GRAPH_QUERY_UNAVAILABLE: Final[str] = "query_graph: graph service unavailable"


def _build_query_graph_spec(handler) -> ToolSpec:
    """Build the `query_graph` ToolSpec wrapper around the given handler.

    The schema MUST stay byte-identical to the in-process plugin's spec
    (`plugins/graph/__init__.py:_register_query_handlers`).
    """
    return ToolSpec(
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
                        "maximum": QUERY_GRAPH_MAX_DEPTH,
                        "default": QUERY_GRAPH_MAX_DEPTH,
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
        handler=handler,
    )


def graph_query_tool_proxy_call(input_: dict, *, user_id: str) -> str:
    """Graph ``query`` proxy for ``KG_SERVICE_URL`` when ``GRAPH_MODE=service``.

    Invokes the KG service's ``graph.query`` tool at ``/tools/query_graph`` over
    the v1 invoke contract: Core wraps the args in the request envelope (with
    ``user_id`` in ``arguments`` for per-user scoping) and returns the parsed
    ``output`` string. On any failure returns :data:`GRAPH_QUERY_UNAVAILABLE`.
    """
    max_depth = input_.get("max_depth")
    if isinstance(max_depth, int) and max_depth > QUERY_GRAPH_MAX_DEPTH:
        _log.warning(
            "query_graph proxy: rejected max_depth=%d > cap %d",
            max_depth,
            QUERY_GRAPH_MAX_DEPTH,
        )
        return GRAPH_QUERY_UNAVAILABLE
    base = config.get_kg_service_url()
    if not base or not str(base).strip():
        return GRAPH_QUERY_UNAVAILABLE
    secret = config.get_kg_webhook_secret()
    bearer = (secret or "").strip() or None

    arguments = dict(input_)
    arguments["user_id"] = user_id
    result = post_capability_tool_invocation(
        base_url=base,
        tool_name="graph.query",
        user_id=user_id,
        arguments=arguments,
        invoke_path="/tools/query_graph",
        timeout_s=QUERY_GRAPH_PROXY_TIMEOUT_S,
        service_bearer=bearer,
        require_service_bearer=config.get_graph_proxy_require_service_bearer(),
        unavailable_message=GRAPH_QUERY_UNAVAILABLE,
    )
    if not result.ok:
        return GRAPH_QUERY_UNAVAILABLE
    return result.output_text


def _query_graph_proxy_handler(input_: dict, *, user_id: str) -> str:
    """POST `input_` to the KG service's `/tools/query_graph` and return the JSON body."""
    return graph_query_tool_proxy_call(input_, user_id=user_id)


def register_query_graph_proxy() -> None:
    """Fire `Event.TOOL_REGISTERED` with the `query_graph` proxy ToolSpec."""
    spec = _build_query_graph_spec(_query_graph_proxy_handler)
    hooks.fire(Event.TOOL_REGISTERED, spec)
    _log.info(
        "query_graph_proxy: registered (KG service mode); requests will be POSTed to %s",
        "{KG_SERVICE_URL}/tools/query_graph",
    )
