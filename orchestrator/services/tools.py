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

        results = semantic_search(query, limit=10)
        return json.dumps(
            {
                "results": [
                    {"path": r.file_path, "text": r.chunk_text, "score": r.score} for r in results
                ],
                "count": len(results),
            }
        )
    except Exception:
        _log.exception("Semantic search failed, falling back to filename search")
        return _fallback_search(query)


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
            "description": "Searches files by name under the configured filesystem root.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Substring to match in filename.",
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
            "input_schema": {
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
