# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Read-only **tool catalog** — a deterministic snapshot of tool surfaces in Core.

Phase 3B: :func:`prepare_llm_tools_for_request` / :func:`try_run_oop_capability_tool`
extend the LLM tool list and dispatch when ``LUMOGIS_TOOL_CATALOG_ENABLED`` is
set; default remains the global ``services.tools.TOOLS`` list.

Classification limits (observable today):
* In-process `ToolSpec` rows do not carry a ``"plugin"`` bit; anything not in
  the three built-in LLM tool names and not the KG service proxy is labeled
  ``source="plugin"`` (true plugin-registered in-process tools).
* ``query_graph`` is ``source="proxy"`` when the handler is
  :func:`services.tools._query_graph_proxy_handler`, else ``source="plugin"``.
* ``actions.registry`` metadata is cross-referenced; actions without a
  matching tool name get their own ``transport="catalog_only"`` rows.
* Per-user Ask/Do for the read model: :func:`build_tool_catalog_for_user` sets
  ``permission_mode`` to ``ask`` / ``do`` / ``blocked`` / ``unknown`` using
  :func:`permissions.get_connector_mode` when ``connector`` is known (FU-3).
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from contextvars import ContextVar
from contextvars import Token
from dataclasses import dataclass
from dataclasses import replace
from typing import Any

from models.capability import CapabilityTool
from models.tool_spec import ToolSpec
from services.capability_registry import CapabilityRegistry
from services.capability_registry import RegisteredService

_log = logging.getLogger(__name__)

# The three first-party tools always present in :mod:`services.tools` before
# plugins. Everything else in ``TOOL_SPECS`` arrived via
# ``Event.TOOL_REGISTERED`` (graph plugin, KG proxy, or other plugins).
_CORE_LLM_TOOL_NAMES: frozenset[str] = frozenset(("search_files", "read_file", "query_entity"))

# Align with remediation plan: core → plugin → mcp → proxy → capability, then
# action registry-only rows.
_SOURCE_ORDER: dict[str, int] = {
    "core": 0,
    "plugin": 1,
    "mcp": 2,
    "proxy": 3,
    "capability": 4,
    "action": 5,
}

_TRANSPORT_ORDER: dict[str, int] = {
    "llm_loop": 0,
    "both": 1,
    "mcp_surface": 2,
    "catalog_only": 3,
}


@dataclass(frozen=True)
class ToolCatalogEntry:
    """One row in the unified catalog (read model)."""

    name: str
    source: str
    source_id: str
    transport: str
    origin_tier: str
    available: bool
    why_not_available: str | None
    connector: str | None = None
    action_type: str | None = None
    permission_mode: str = "unknown"
    requires_credentials: bool | None = None
    capability_id: str | None = None
    tool_schema: dict[str, Any] | None = None
    is_write: bool = False


@dataclass(frozen=True)
class ToolCatalog:
    """Ordered snapshot produced by :func:`build_tool_catalog`."""

    entries: tuple[ToolCatalogEntry, ...]


def _sort_entries(entries: list[ToolCatalogEntry]) -> tuple[ToolCatalogEntry, ...]:
    return tuple(
        sorted(
            entries,
            key=lambda e: (
                _SOURCE_ORDER.get(e.source, 99),
                _TRANSPORT_ORDER.get(e.transport, 99),
                e.name,
                e.capability_id or "",
                e.source_id,
            ),
        )
    )


def _classify_inprocess_spec(
    spec: ToolSpec, *, query_graph_proxy_handler: Any
) -> tuple[str, str, str, str, str | None, bool]:
    """Return (source, source_id, transport, origin_tier, why_na, available)."""
    if spec.name in _CORE_LLM_TOOL_NAMES:
        return ("core", "core", "llm_loop", "local", None, True)
    if spec.name == "query_graph" and spec.handler is query_graph_proxy_handler:
        return (
            "proxy",
            "lumogis-graph:service",
            "llm_loop",
            "capability_backed",
            None,
            True,
        )
    if spec.name == "query_graph":
        return ("plugin", "plugin:graph", "llm_loop", "plugin", None, True)
    return ("plugin", "plugin:unknown", "llm_loop", "plugin", None, True)


def _entries_for_tool_specs(
    specs: list[ToolSpec],
) -> list[ToolCatalogEntry]:
    from services import tools as tools_mod

    out: list[ToolCatalogEntry] = []
    for spec in specs:
        source, source_id, transport, origin_tier, why, avail = _classify_inprocess_spec(
            spec,
            query_graph_proxy_handler=tools_mod._query_graph_proxy_handler,
        )
        out.append(
            ToolCatalogEntry(
                name=spec.name,
                source=source,
                source_id=source_id,
                transport=transport,
                origin_tier=origin_tier,
                available=avail,
                why_not_available=why,
                connector=spec.connector,
                action_type=spec.action_type,
                permission_mode="unknown",
                requires_credentials=None,
                capability_id=None,
                tool_schema=dict(spec.definition) if spec.definition else None,
                is_write=spec.is_write,
            )
        )
    return out


def _entries_for_mcp(mcp_tools: list[CapabilityTool]) -> list[ToolCatalogEntry]:
    out: list[ToolCatalogEntry] = []
    for t in mcp_tools:
        out.append(
            ToolCatalogEntry(
                name=t.name,
                source="mcp",
                source_id="mcp:core_surface",
                transport="mcp_surface",
                origin_tier="mcp_only",
                available=True,
                why_not_available=None,
                connector=None,
                action_type=None,
                permission_mode="unknown",
                requires_credentials=None,
                capability_id=None,
                tool_schema=t.model_dump(),
                is_write=False,
            )
        )
    return out


def _permission_connector_for(manifest) -> str:
    if manifest.permissions_required:
        return str(manifest.permissions_required[0])
    return f"capability.{manifest.id}"


def _entries_for_capability_registry(registry: CapabilityRegistry) -> list[ToolCatalogEntry]:
    out: list[ToolCatalogEntry] = []
    for svc in registry.all_services():
        out.extend(_entries_for_capability_service(svc))
    return out


def _entries_for_capability_service(svc: RegisteredService) -> list[ToolCatalogEntry]:
    out: list[ToolCatalogEntry] = []
    mid = svc.manifest.id
    healthy = bool(svc.healthy)
    why: str | None = None if healthy else "capability service not healthy (last probe failed)"
    conn = _permission_connector_for(svc.manifest)
    for t in svc.manifest.tools:
        out.append(
            ToolCatalogEntry(
                name=t.name,
                source="capability",
                source_id=mid,
                transport="catalog_only",
                origin_tier="capability_backed",
                available=healthy,
                why_not_available=why,
                connector=conn,
                action_type=t.name,
                permission_mode="unknown",
                requires_credentials=None,
                capability_id=mid,
                tool_schema=t.model_dump(),
                is_write=False,
            )
        )
    return out


def _entries_for_actions(
    actions: list[dict[str, Any]],
    tool_names: set[str],
) -> list[ToolCatalogEntry]:
    """Add catalog_only rows for actions that are not also LLM tool names."""
    out: list[ToolCatalogEntry] = []
    for a in actions:
        name = a.get("name")
        if not name or name in tool_names:
            continue
        out.append(
            ToolCatalogEntry(
                name=name,
                source="action",
                source_id="action_registry",
                transport="catalog_only",
                origin_tier="local",
                available=True,
                why_not_available=None,
                connector=a.get("connector"),
                action_type=a.get("action_type"),
                permission_mode="unknown",
                requires_credentials=None,
                capability_id=None,
                tool_schema=a.get("definition") if isinstance(a.get("definition"), dict) else None,
                is_write=bool(a.get("is_write")),
            )
        )
    return out


def build_tool_catalog(
    *,
    tool_specs: list[ToolSpec] | None = None,
    mcp_tools: list[CapabilityTool] | None = None,
    capability_registry: CapabilityRegistry | None = None,
    list_actions_fn: Callable[[], list[dict]] | None = None,
) -> ToolCatalog:
    """Assemble a sorted read-only :class:`ToolCatalog`.

    Parameters are injectable for hermetic unit tests. Defaults pull live
    registries (``services.tools.TOOL_SPECS``, ``mcp_server.MCP_TOOLS``,
    ``config.get_capability_registry()``, ``actions.registry.list_actions``).
    """
    from services import tools as tools_mod

    if tool_specs is None:
        tool_specs = list(tools_mod.TOOL_SPECS)
    if mcp_tools is None:
        from mcp_server import MCP_TOOLS_FOR_MANIFEST

        mcp_tools = list(MCP_TOOLS_FOR_MANIFEST)
    if capability_registry is None:
        import config

        capability_registry = config.get_capability_registry()
    if list_actions_fn is None:
        from actions.registry import list_actions

        list_actions_fn = list_actions

    combined: list[ToolCatalogEntry] = []
    combined.extend(_entries_for_tool_specs(tool_specs))
    combined.extend(_entries_for_mcp(mcp_tools))
    combined.extend(_entries_for_capability_registry(capability_registry))
    tool_names = {s.name for s in tool_specs}
    combined.extend(_entries_for_actions(list_actions_fn(), tool_names))
    return ToolCatalog(entries=_sort_entries(combined))


def _permission_mode_for_catalog_entry(
    user_id: str,
    entry: ToolCatalogEntry,
    *,
    get_connector_mode_fn: Callable[..., str] | None = None,
) -> str:
    """Map connector + Ask/Do to a façade-safe ``permission_mode`` string."""
    if not entry.connector or not str(entry.connector).strip():
        return "unknown"
    if get_connector_mode_fn is not None:
        getter = get_connector_mode_fn
    else:
        from permissions import get_connector_mode as getter
    try:
        raw = getter(user_id=user_id, connector=entry.connector)
    except Exception:
        _log.warning(
            "tool_catalog.permission_mode_failed",
            extra={"tool": entry.name, "connector": entry.connector},
            exc_info=True,
        )
        return "unknown"
    if not isinstance(raw, str):
        return "unknown"
    mode = raw.strip().upper()
    if mode == "DO":
        return "do"
    if mode == "ASK":
        return "blocked" if entry.is_write else "ask"
    return "unknown"


def build_tool_catalog_for_user(
    user_id: str | None = None,
    *,
    get_connector_mode_fn: Callable[..., str] | None = None,
    **kwargs: Any,
) -> ToolCatalog:
    """Build the catalog; resolve ``permission_mode`` per user when possible.

    ``permission_mode`` values: ``unknown`` (no connector or lookup error),
    ``do`` (connector in DO mode), ``ask`` (ASK mode, read-only tool),
    ``blocked`` (ASK mode but tool is a write).
    """
    cat = build_tool_catalog(**kwargs)
    if not (isinstance(user_id, str) and user_id.strip()):
        return cat
    uid = user_id.strip()
    resolved = tuple(
        replace(
            e,
            permission_mode=_permission_mode_for_catalog_entry(
                uid, e, get_connector_mode_fn=get_connector_mode_fn
            ),
        )
        for e in cat.entries
    )
    return ToolCatalog(entries=resolved)


# ---------------------------------------------------------------------------
# Phase 3B — LLM tool view (flag-gated) + OOP dispatch context
# ---------------------------------------------------------------------------

OOP_HTTP_TIMEOUT_S: float = 10.0


@dataclass(frozen=True)
class OopCapabilityToolRoute:
    """Metadata for an executable out-of-process capability tool (one request)."""

    base_url: str
    capability_id: str
    tool_name: str
    connector: str
    action_type: str
    is_write: bool
    require_bearer: bool
    get_bearer: Callable[[], str | None]


# Maps tool name -> route for the current ``loop.ask`` / ``ask_stream`` only.
OOP_TOOL_ROUTES: ContextVar[dict[str, OopCapabilityToolRoute] | None] = ContextVar(
    "oop_tool_routes",
    default=None,
)


def _openai_def_from_capability_tool(t: CapabilityTool) -> dict[str, Any]:
    """``CapabilityTool`` → same dict shape as ``ToolSpec.definition`` (OpenAI tools)."""
    return {
        "name": t.name,
        "description": t.description,
        "parameters": t.input_schema
        if isinstance(t.input_schema, dict) and t.input_schema
        else {"type": "object", "properties": {}},
    }


def _collect_oop_eligible(
    registry: CapabilityRegistry,
    base_names: set[str],
) -> tuple[dict[str, OopCapabilityToolRoute], list[dict[str, Any]]]:
    """Return routes + tool definitions, deterministic order, fail-closed without bearer."""
    import config

    routes: dict[str, OopCapabilityToolRoute] = {}
    extra_defs: list[dict[str, Any]] = []
    services = sorted(registry.all_services(), key=lambda s: s.manifest.id)
    for svc in services:
        if not svc.healthy:
            continue
        mid = svc.manifest.id
        base = (svc.base_url or "").rstrip("/")
        if not base:
            continue
        bearer = config.get_capability_bearer_for_service(mid)
        if not (bearer and bearer.strip()):
            # Generic OOP path requires a configured per-service token (fail-closed).
            continue
        m = svc.manifest
        conn = _permission_connector_for(m)
        for ct in sorted(m.tools, key=lambda t: t.name):
            if ct.name in base_names or ct.name in routes:
                continue

            def _bearer_for_tool(service_id: str = mid) -> str | None:
                return config.get_capability_bearer_for_service(service_id)

            routes[ct.name] = OopCapabilityToolRoute(
                base_url=base,
                capability_id=mid,
                tool_name=ct.name,
                connector=conn,
                action_type=ct.name,
                is_write=False,
                require_bearer=True,
                get_bearer=_bearer_for_tool,
            )
            extra_defs.append(_openai_def_from_capability_tool(ct))
    return routes, extra_defs


def prepare_llm_tools_for_request(
    user_id: str,
    *,
    capability_registry: CapabilityRegistry | None = None,
) -> tuple[list[dict], Token | None]:
    """Build the list passed as ``tools=`` to the LLM (does not mutate ``TOOLS``).

    When ``LUMOGIS_TOOL_CATALOG_ENABLED`` is false or on error, returns the
    same ``services.tools.TOOLS`` object for identity/parity. When the flag
    is true and at least one OOP tool is eligible, returns a *new* list
    (base copy + extra defs) and sets :data:`OOP_TOOL_ROUTES` for the request.

    The ``user_id`` is reserved for a future per-user filter; discovery is
    global today.
    """
    del user_id
    from services import tools as tools_mod

    if not _catalog_flag():
        return tools_mod.TOOLS, None
    if capability_registry is None:
        import config

        capability_registry = config.get_capability_registry()
    try:
        base = tools_mod.TOOLS
        base_names = {s.name for s in tools_mod.TOOL_SPECS}
        routes, extras = _collect_oop_eligible(capability_registry, base_names)
        if not routes:
            return base, None
        merged = list(base) + extras
        tok = OOP_TOOL_ROUTES.set(routes)
        return merged, tok
    except Exception:
        _log.warning("prepare_llm_tools_for_request: falling back to TOOLS", exc_info=True)
        return tools_mod.TOOLS, None


def _catalog_flag() -> bool:
    import config

    return bool(config.get_tool_catalog_enabled())


def finish_llm_tools_request(token: Token | None) -> None:
    """Reset :data:`OOP_TOOL_ROUTES` after a chat request (``loop``)."""
    if token is not None:
        OOP_TOOL_ROUTES.reset(token)


def try_run_oop_capability_tool(name: str, input_: dict, *, user_id: str) -> str | None:
    """If the current request registered an OOP route for *name*, execute it.

    Returns ``None`` if this is not an OOP tool for this request. Otherwise
    returns a string (JSON error or tool body text) suitable for the LLM.
    """
    from services.execution import PermissionCheck
    from services.execution import ToolAuditEnvelope
    from services.execution import ToolExecutor
    from services.execution import persist_tool_audit_envelope

    import config

    if not config.get_tool_catalog_enabled():
        return None
    ctx = OOP_TOOL_ROUTES.get() or {}
    route = ctx.get(name)
    if route is None:
        return None
    svc = config.get_capability_registry().get_service(route.capability_id)
    if svc is None or not svc.healthy:
        return json.dumps({"error": "capability service unavailable"})

    def _emit(e: ToolAuditEnvelope) -> None:
        _log.info(
            "oop_tool_audit",
            extra={
                "status": e.status,
                "user_id": e.user_id,
                "tool_name": e.tool_name,
                "capability_id": e.capability_id,
                "request_id": e.request_id,
                "failure_reason": e.failure_reason,
            },
        )
        persist_tool_audit_envelope(e)

    ex = ToolExecutor(permission=PermissionCheck(), emit_audit=_emit)
    res = ex.execute_capability_http(
        user_id=user_id,
        request_id=str(uuid.uuid4()),
        tool_name=route.tool_name,
        capability_id=route.capability_id,
        connector=route.connector,
        action_type=route.action_type,
        is_write=route.is_write,
        base_url=route.base_url,
        input_=input_,
        get_service_bearer=route.get_bearer,
        require_service_bearer=route.require_bearer,
        service_healthy=True,
        timeout_s=OOP_HTTP_TIMEOUT_S,
    )
    if res.denied:
        return json.dumps(
            {
                "error": "Permission denied",
                "connector": route.connector,
                "action": route.action_type,
            }
        )
    if not res.success:
        return res.output
    return res.output
