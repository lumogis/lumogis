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
import os
from datetime import datetime
from datetime import timezone

import hooks
from events import Event
from models.tool_spec import ToolSpec
from services.injection_sanitiser import ResolvedOrigin
from services.injection_sanitiser import redact_for_log
from services.injection_sanitiser import sanitise_at_ingest
from services.injection_sanitiser import sanitize_attribute_source_token
from services.injection_sanitiser import wrap_retrieved_chunk

import config

_log = logging.getLogger(__name__)


def _origin_from_search_metadata(metadata: dict | None) -> ResolvedOrigin:
    iso_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    md = metadata or {}
    om = md.get("origin")

    if isinstance(om, dict):
        scope_raw = str(om.get("scope") or "personal")
        hits = list(om.get("pattern_hits") or [])

        return {
            "trusted": bool(om.get("trusted", False)),
            "scope": (
                scope_raw if scope_raw in ("personal", "shared", "system", "unknown") else "unknown"
            ),
            "source": str(
                om.get("source") or sanitize_attribute_source_token(md.get("file_path", "legacy"))
            ),
            "session_id": om.get("session_id"),
            "ingested": str(om.get("ingested") or iso_now),
            "pattern_hits": hits,
            "pre_wrapped": bool(om.get("pre_wrapped", False)),
        }

    scope_payload = str(md.get("scope") or "unknown")

    return {
        "trusted": False,
        "scope": scope_payload if scope_payload in ("personal", "shared", "system") else "unknown",
        "source": sanitize_attribute_source_token(str(md.get("file_path") or "legacy")),
        "session_id": None,
        "ingested": iso_now,
        "pattern_hits": [],
    }


def _tool_body_from_chunk(chunk_text: str, metadata: dict | None, *, user_id: str) -> str:
    """Truncate plaintext before escaping/wrapping + optional context rescan."""

    body_plain = chunk_text[:500]

    if not config.is_injection_sanitiser_enabled():
        return body_plain

    rescan = os.environ.get("INJECTION_CONTEXT_RESCAN", "false").strip().lower() in (
        "true",
        "1",
        "yes",
        "on",
    )

    if rescan:
        import config as _cfg

        outcome = sanitise_at_ingest(
            body_plain,
            scanner=_cfg.get_injection_scanner(),
            skip_if_empty=False,
        )
        body_plain = outcome["text"]
        hooks.fire_background(
            Event.INJECTION_FLAGGED,
            user_id=user_id,
            source=_origin_from_search_metadata(metadata)["source"],
            file_path="<tool_search_hit>",
            chunk_index=None,
            severity=outcome["max_severity"],
            action=os.environ.get("INJECTION_ACTION", "wrap"),
            pattern_hits=outcome["pattern_hits"],
            sanitised_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            stage="tool_result",
            text_probe=redact_for_log(outcome["text"]),
        )

    resolved = _origin_from_search_metadata(metadata)

    if resolved.get("pre_wrapped"):
        return chunk_text[:500]

    flagged = bool(resolved.get("pattern_hits"))
    return wrap_retrieved_chunk(body_plain, resolved, injection_flagged=flagged)


def _search_files(
    input_: dict,
    *,
    user_id: str,
    auto_rag_point_ids: set[str] | None = None,
) -> str:
    query = input_.get("query", "")
    try:
        from services.search import semantic_search

        results = semantic_search(query, limit=5, user_id=user_id)
        filtered = []
        for r in results:
            if (
                auto_rag_point_ids is not None
                and r.point_id is not None
                and r.point_id in auto_rag_point_ids
            ):
                continue
            filtered.append(r)
        return json.dumps(
            {
                "results": [
                    {
                        "path": r.file_path,
                        "text": _tool_body_from_chunk(r.chunk_text, r.metadata, user_id=user_id),
                        "score": r.score,
                    }
                    for r in filtered
                ],
                "count": len(filtered),
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


def run_tool(
    name: str,
    input_: dict,
    *,
    user_id: str,
    auto_rag_point_ids: set[str] | None = None,
) -> str:
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
        if spec.name == "search_files" and auto_rag_point_ids is not None:
            return spec.handler(input_, user_id=user_id, auto_rag_point_ids=auto_rag_point_ids)
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
