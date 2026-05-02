# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""
Tool registry and executor for the Lumogis orchestrator.

TOOLS is a list[ToolSpec]. run_tool() looks up the spec by name,
calls check_permission() using the spec's safety metadata, then
executes the handler. Plugins register tools by firing
Event.TOOL_REGISTERED with a ToolSpec object.
"""

import json
import logging

import hooks
from events import Event
from models.tool_spec import ToolSpec
from services.capability_http import QUERY_GRAPH_MAX_DEPTH
from services.capability_http import graph_query_tool_proxy_call

_log = logging.getLogger(__name__)


def _search_files(input_: dict, *, user_id: str) -> str:
    query = input_.get("query", "")
    try:
        from services.search import semantic_search

        results = semantic_search(query, limit=5, user_id=user_id)
        return json.dumps(
            {
                "results": [
                    {
                        "path": r.file_path,
                        "text": r.chunk_text[:500],
                        "score": r.score,
                    }
                    for r in results
                ],
                "count": len(results),
            }
        )
    except Exception:
        _log.exception("Semantic search failed, falling back to filename search")
        return _fallback_search(query)


def _query_entity(input_: dict, *, user_id: str) -> str:
    """Look up what Lumogis knows about a named entity.

    Searches Postgres by exact name / alias match first, then falls back to
    Qdrant semantic similarity. Returns entity metadata and every session /
    document the entity was mentioned in (last 10 appearances).
    """
    name = (input_.get("name") or "").strip()
    if not name:
        return json.dumps({"error": "name is required"})

    try:
        import config as _cfg

        ms = _cfg.get_metadata_store()
        embedder = _cfg.get_embedder()
        vs = _cfg.get_vector_store()

        # Both lookup paths (Postgres exact-match, Qdrant semantic fallback)
        # MUST resolve through the same household visibility rule so a
        # `shared` / `system` entity is reachable from either side. Asymmetry
        # here is a real sharing leak: Alice publishes "Friday meal plan"
        # as `shared`; Bob's exact "meal plan" lookup hits the Postgres path
        # and finds it, but Bob's near-miss "what's for dinner" lookup falls
        # through to Qdrant — and a `user_id`-only payload filter would
        # silently drop the shared row. See plan §2.6 retrieval rule and
        # §8 Qdrant filter shape.
        from auth import UserContext
        from visibility import visible_filter
        from visibility import visible_qdrant_filter

        ctx = UserContext(user_id=user_id)
        vis_clause, vis_params = visible_filter(ctx)
        row = ms.fetch_one(
            "SELECT entity_id, name, entity_type, aliases, context_tags, mention_count "
            "FROM entities "
            f"WHERE {vis_clause} "
            "  AND (lower(name) = lower(%s) "
            "       OR lower(%s) = ANY(SELECT lower(a) FROM unnest(aliases) a))",
            (*vis_params, name, name),
        )

        if row:
            entity_id = row["entity_id"]
            entity_meta = {
                "name": row["name"],
                "type": row["entity_type"],
                "aliases": row["aliases"],
                "context_tags": row["context_tags"],
                "mention_count": row["mention_count"],
            }
        else:
            # Qdrant semantic fallback — household-visible payload filter
            # mirrors the Postgres path above.
            vector = embedder.embed(name)
            hits = vs.search(
                collection="entities",
                vector=vector,
                limit=1,
                threshold=0.75,
                filter=visible_qdrant_filter(ctx),
            )
            if not hits:
                return json.dumps({"found": False, "name": name})
            top_payload = hits[0].get("payload", {})
            entity_id = top_payload.get("entity_id")
            entity_meta = {
                "name": top_payload.get("name", name),
                "type": top_payload.get("entity_type"),
                "aliases": top_payload.get("aliases", []),
                "context_tags": top_payload.get("context_tags", []),
                "mention_count": None,
            }

        # Fetch provenance edges (last 10 distinct evidence mentions).
        #
        # DISTINCT ON (evidence_id, relation_type) is defence-in-depth: the
        # post-012 UNIQUE(source_id, evidence_id, relation_type, user_id)
        # constraint already prevents duplicates at write time. The subquery
        # wrapper preserves "10 most recent distinct mentions" semantics —
        # the inner ORDER BY is required by DISTINCT ON to pick one row per
        # group, but it would otherwise sort the result alphabetically by
        # evidence_id. The outer ORDER BY restores recency order.
        #
        # Why no `WHERE user_id = %s` on this SELECT (visibility contract):
        # the entity_id reaching this point was resolved upstream in this
        # same function under the household visibility rule — both the
        # Postgres lookup (visible_filter) and the Qdrant fallback
        # (visible_qdrant_filter) admit only entities the caller is
        # allowed to see (own personal + all shared/system). entity_relations
        # carries no `scope` column of its own (plan §2.4 rule 9 — visibility
        # inherits from endpoints), so once the endpoint is visible, every
        # relation rooted there is visible too, regardless of which user_id
        # owns the relation row. Adding a user_id predicate here would
        # silently strip the cross-user evidence on shared/system entities,
        # which is the *intended* household behaviour. The visibility
        # contract is owned by _query_entity's resolution path above.
        appearances: list[dict] = []
        if entity_id:
            relations = ms.fetch_all(
                "SELECT relation_type, evidence_type, evidence_id, created_at "
                "FROM ("
                "  SELECT DISTINCT ON (evidence_id, relation_type) "
                "    relation_type, evidence_type, evidence_id, created_at "
                "  FROM entity_relations "
                "  WHERE source_id = %s "
                "  ORDER BY evidence_id, relation_type, created_at DESC"
                ") sub "
                "ORDER BY created_at DESC "
                "LIMIT 10",
                (entity_id,),
            )
            appearances = [
                {
                    "type": r["relation_type"],
                    "evidence_type": r["evidence_type"],
                    "evidence_id": r["evidence_id"],
                    "at": str(r["created_at"]),
                }
                for r in relations
            ]

        return json.dumps(
            {
                "found": True,
                "entity": entity_meta,
                "appearances": appearances,
            }
        )

    except Exception:
        _log.exception("query_entity failed for name=%r", name)
        return json.dumps({"error": "entity lookup failed", "name": name})


def _fallback_search(query: str) -> str:
    from services.search import fuzzy_filename_search

    hits = fuzzy_filename_search(query)
    return json.dumps({"results": hits, "count": len(hits)})


def _read_file(input_: dict, *, user_id: str) -> str:
    # `user_id` is accepted for signature uniformity (run_tool always passes it)
    # so the FILESYSTEM_ROOT-scoped tool participates in the same audit /
    # propagation contract as its peers, even though access is currently
    # gated by FILESYSTEM_ROOT alone — when per-user filesystem roots land
    # this hook is the single point that has to learn about them.
    del user_id
    import os
    from pathlib import Path

    path = input_.get("path", "")
    fs_root = os.environ.get("FILESYSTEM_ROOT", "")
    if fs_root:
        resolved = str(Path(path).resolve())
        allowed = str(Path(fs_root).resolve())
        if not resolved.startswith(allowed + "/") and resolved != allowed:
            return json.dumps(
                {
                    "error": f"Access denied: path is outside FILESYSTEM_ROOT ({allowed})",
                    "path": path,
                }
            )

    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(3000)
        truncated = len(content) >= 3000
        return json.dumps({"content": content, "truncated": truncated, "path": path})
    except Exception as e:
        return json.dumps({"error": str(e), "path": path})


TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="search_files",
        connector="filesystem-mcp",
        action_type="search_files",
        is_write=False,
        definition={
            "name": "search_files",
            "description": (
                "Semantic search over indexed files. Returns the top 5 "
                "matching text chunks with file paths and relevance scores. "
                "Use a single broad query — do not call repeatedly with "
                "slight variations. Use read_file to inspect a specific result."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query.",
                    }
                },
                "required": ["query"],
            },
        },
        handler=_search_files,
    ),
    ToolSpec(
        name="read_file",
        connector="filesystem-mcp",
        action_type="read_file",
        is_write=False,
        definition={
            "name": "read_file",
            "description": "Reads file contents (first 3000 characters).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file.",
                    }
                },
                "required": ["path"],
            },
        },
        handler=_read_file,
    ),
    ToolSpec(
        name="query_entity",
        connector="lumogis-memory",
        action_type="query_entity",
        is_write=False,
        definition={
            "name": "query_entity",
            "description": (
                "Look up everything Lumogis knows about a named person, "
                "organisation, project, or concept. Returns entity metadata "
                "(type, aliases, context tags, mention count) and a list of "
                "sessions and documents where the entity appeared. "
                "Use this when asked 'what do you know about [name]?'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the entity to look up.",
                    }
                },
                "required": ["name"],
            },
        },
        handler=_query_entity,
    ),
]

TOOLS = [spec.definition for spec in TOOL_SPECS]


def _check_permission(connector: str, action_type: str, is_write: bool, *, user_id: str) -> bool:
    from permissions import check_permission

    return check_permission(connector, action_type, is_write, user_id=user_id)


def run_tool(name: str, input_: dict, *, user_id: str) -> str:
    """Look up ToolSpec, check permission, execute handler.

    ``user_id`` is keyword-only and **required** in Phase 3 — tool calls
    fan out to per-user data stores and must carry the caller's identity
    end-to-end. Callers that forget it raise :class:`TypeError` at the
    boundary instead of silently degrading to the legacy ``"default"``
    bucket.

    Plugin-supplied handlers may have been authored against the legacy
    one-arg signature; for those we transparently fall back to
    ``handler(input_)`` so existing plugins keep working until they opt
    into the multi-user contract.
    """
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("run_tool: user_id (keyword-only) is required")

    spec = next((s for s in TOOL_SPECS if s.name == name), None)
    if spec is None:
        from services.unified_tools import try_run_oop_capability_tool

        oop_out = try_run_oop_capability_tool(name, input_, user_id=user_id)
        if oop_out is not None:
            return oop_out
        return json.dumps({"error": f"Unknown tool: {name}"})

    if not _check_permission(spec.connector, spec.action_type, spec.is_write, user_id=user_id):
        return json.dumps(
            {
                "error": "Permission denied",
                "connector": spec.connector,
                "action": spec.action_type,
                "detail": f"Connector '{spec.connector}' is in ASK mode; writes blocked.",
            }
        )

    try:
        return spec.handler(input_, user_id=user_id)
    except TypeError as exc:
        if "user_id" in str(exc):
            _log.warning(
                "Tool %r handler does not accept user_id kwarg — falling back "
                "to legacy single-arg signature; update the plugin to "
                "accept ``user_id`` keyword to participate in user isolation.",
                spec.name,
            )
            return spec.handler(input_)
        raise


def _add_plugin_tool(spec: ToolSpec) -> None:
    """Listener for Event.TOOL_REGISTERED — plugins register tools via hooks."""
    if not isinstance(spec, ToolSpec):
        _log.error("TOOL_REGISTERED expects ToolSpec, got %s", type(spec).__name__)
        return
    TOOL_SPECS.append(spec)
    TOOLS.append(spec.definition)
    _log.info("Plugin tool registered: %s (connector=%s)", spec.name, spec.connector)


hooks.register(Event.TOOL_REGISTERED, _add_plugin_tool)


# ---------------------------------------------------------------------------
# query_graph proxy — used when GRAPH_MODE=service
# ---------------------------------------------------------------------------
#
# When the graph plugin runs out-of-process (`lumogis-graph` service), the
# in-process `plugins/graph/__init__.py` short-circuits and never registers a
# `query_graph` ToolSpec (see plugin's mode guard). Without that ToolSpec the
# LLM has no way to issue `mode=ego/path/mentions` queries against the KG.
#
# `register_query_graph_proxy()` is the substitute: it builds a ToolSpec with
# the EXACT same JSON schema (so prompts, fine-tunes, and existing eval suites
# need no change) and a handler that POSTs to the KG service's stable
# `POST /tools/query_graph` endpoint. `services/lumogis-graph/routes/tools.py`
# wraps `graph.query.query_graph_tool(input_)` and enforces a 2 s wall-clock
# budget; on timeout it returns 504 with `{"reason": "timeout"}` and the proxy
# below converts that into a user-friendly string so the LLM can recover
# mid-conversation rather than seeing a Python exception.
#
# Why a separate HTTP endpoint and not `/mcp`:
#   `/mcp` is streamable HTTP for MCP clients (Thunderbolt and friends);
#   it requires session negotiation. Core's tool-call path is a one-shot
#   request/response — `/tools/query_graph` matches that contract directly
#   and keeps the LLM tool-loop latency low.


def _build_query_graph_spec(handler) -> ToolSpec:
    """Build the `query_graph` ToolSpec wrapper around the given handler.

    The schema MUST stay byte-identical to the in-process plugin's spec
    (`plugins/graph/__init__.py:_register_query_handlers`). If you change
    one you MUST change the other or the LLM tool-call signature will
    drift between `inprocess` and `service` modes — the
    `test_register_query_graph_proxy_schema_matches_plugin` regression
    pins this.
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


def _query_graph_proxy_handler(input_: dict, *, user_id: str) -> str:
    """POST `input_` to the KG service's `/tools/query_graph` and return the JSON body.

    Delegates to :func:`services.capability_http.graph_query_tool_proxy_call`
    (reusable HTTP helper; preserves legacy unauthenticated-POST when
    ``GRAPH_WEBHOOK_SECRET`` is unset).

    Returns a stringified error message (NOT JSON) on every failure path;
    successful 200 responses are the response body text unchanged.
    """
    return graph_query_tool_proxy_call(input_, user_id=user_id)


def register_query_graph_proxy() -> None:
    """Fire `Event.TOOL_REGISTERED` with the `query_graph` proxy ToolSpec.

    Called from `orchestrator/main.py` lifespan when `GRAPH_MODE=service`.
    Idempotent at the dispatcher layer: if called twice, `_add_plugin_tool`
    appends two specs to TOOL_SPECS, the second one shadowing the first
    via `next(...)` lookup in `run_tool` — but the lifespan only calls this
    helper once per process, so duplicate registration is not a real
    concern. We still log INFO so operators can confirm the wiring fired.
    """
    spec = _build_query_graph_spec(_query_graph_proxy_handler)
    hooks.fire(Event.TOOL_REGISTERED, spec)
    _log.info(
        "query_graph_proxy: registered (KG service mode); requests will be POSTed to %s",
        "{KG_SERVICE_URL}/tools/query_graph",
    )
