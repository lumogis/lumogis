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

_log = logging.getLogger(__name__)


def _search_files(input_: dict) -> str:
    query = input_.get("query", "")
    try:
        from services.search import semantic_search

        results = semantic_search(query, limit=5)
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


def _query_entity(input_: dict) -> str:
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

        # Postgres lookup: canonical name or alias match
        row = ms.fetch_one(
            "SELECT entity_id, name, entity_type, aliases, context_tags, mention_count "
            "FROM entities "
            "WHERE lower(name) = lower(%s) "
            "   OR lower(%s) = ANY(SELECT lower(a) FROM unnest(aliases) a)",
            (name, name),
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
            # Qdrant semantic fallback
            vector = embedder.embed(name)
            hits = vs.search(
                collection="entities",
                vector=vector,
                limit=1,
                threshold=0.75,
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

        # Fetch provenance edges (last 10)
        appearances: list[dict] = []
        if entity_id:
            relations = ms.fetch_all(
                "SELECT relation_type, evidence_type, evidence_id, created_at "
                "FROM entity_relations "
                "WHERE source_id = %s "
                "ORDER BY created_at DESC LIMIT 10",
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


def _read_file(input_: dict) -> str:
    path = input_.get("path", "")
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


def _check_permission(connector: str, action_type: str, is_write: bool) -> bool:
    from permissions import check_permission

    return check_permission(connector, action_type, is_write)


def run_tool(name: str, input_: dict) -> str:
    """Look up ToolSpec, check permission, execute handler."""
    spec = next((s for s in TOOL_SPECS if s.name == name), None)
    if spec is None:
        return json.dumps({"error": f"Unknown tool: {name}"})

    if not _check_permission(spec.connector, spec.action_type, spec.is_write):
        return json.dumps(
            {
                "error": "Permission denied",
                "connector": spec.connector,
                "action": spec.action_type,
                "detail": f"Connector '{spec.connector}' is in ASK mode; writes blocked.",
            }
        )

    return spec.handler(input_)


def _add_plugin_tool(spec: ToolSpec) -> None:
    """Listener for Event.TOOL_REGISTERED — plugins register tools via hooks."""
    if not isinstance(spec, ToolSpec):
        _log.error("TOOL_REGISTERED expects ToolSpec, got %s", type(spec).__name__)
        return
    TOOL_SPECS.append(spec)
    TOOLS.append(spec.definition)
    _log.info("Plugin tool registered: %s (connector=%s)", spec.name, spec.connector)


hooks.register(Event.TOOL_REGISTERED, _add_plugin_tool)
