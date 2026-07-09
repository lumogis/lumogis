# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Lumogis Core MCP server surface (Area 4 ecosystem plumbing).

Exposes a stable, community-tier subset of Lumogis as MCP tools so external
clients (Thunderbolt, Claude Desktop, other MCP-speaking agents) can call
into Lumogis as infrastructure rather than only consuming it through the
LibreChat UI.

Transport
---------
Streamable HTTP, **stateless**, JSON-only responses, mounted at /mcp on the
existing FastAPI orchestrator (single port, port 8000 by default).

Stateless mode (`stateless_http=True, json_response=True`) is a deliberate
choice for two reasons:

1. It sidesteps the well-known "Task group is not initialized" trap when
   mounting the MCP SDK's Starlette sub-app inside FastAPI's lifespan.
   Stateless servers do not start a session manager, so no lifespan
   merging is required.
2. The five community tools are all read-only and self-contained. None of
   them benefits from session state, server→client notifications, or
   long-lived streams. A future stateful MCP server (e.g. for long-running
   KG queries) belongs in a standalone capability service, not Core.

Graceful degradation
--------------------
If the `mcp` package is not installed at import time (e.g. a slimmed-down
test environment), the module exposes `mcp = None` and Core boots normally
with no MCP surface. `routes/capabilities.py` and `main.py` both check for
this and skip MCP-related wiring without raising.

Tool ↔ service mapping
----------------------
- memory.search       -> services.memory.retrieve_context
- memory.get_recent   -> services.memory.recent_sessions
- entity.lookup       -> services.entities.lookup_by_name
- entity.search       -> services.entities.search_by_name
- context.build       -> services.search.semantic_search +
                         services.memory.retrieve_context +
                         services.context_budget.truncate_text

Tools are thin wrappers — no business logic lives here.
"""

from __future__ import annotations

import logging
import os
from contextvars import ContextVar
from typing import Any

from __version__ import __version__ as CORE_VERSION
from models.capability import CapabilityLicenseMode
from models.capability import CapabilityManifest
from models.capability import CapabilityMaturity
from models.capability import CapabilityTool
from models.capability import CapabilityTransport

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase 3: per-call user resolution.
#
# Lumogis MCP runs on the same process as the orchestrator. External MCP
# clients (Thunderbolt, Claude Desktop, …) authenticate via
# ``MCP_AUTH_TOKEN`` (a coarse "who can talk to MCP at all" gate) and
# *also* — when the operator sets up the optional Lumogis JWT bridge —
# present a Bearer JWT minted by ``orchestrator/auth.py``. The MCP tools
# need a ``user_id`` to scope per-user queries.
#
# Resolution rule (applied per tool call by ``_resolve_user_id``):
#
#   1. The MCP transport injects the inbound request's ``Authorization``
#      header into a context-local. If a valid Lumogis JWT is present we
#      use its ``sub`` claim — this is the only path that gives real
#      multi-user isolation over MCP.
#   2. Otherwise we fall back to the operator-configured
#      ``MCP_DEFAULT_USER_ID`` env var (a single user_id assigned to the
#      shared MCP token). Self-hosted single-user installs set this to
#      the bootstrap admin's id.
#   3. If neither is set we raise — tools refuse to execute rather than
#      silently leaking/writing into the legacy ``"default"`` bucket.
#
# The legacy module-level ``_DEFAULT_USER_ID = "default"`` constant is
# **gone**. Any caller that wants a stable per-process default must set
# ``MCP_DEFAULT_USER_ID`` explicitly.
# ---------------------------------------------------------------------------


def _resolve_user_id() -> str:
    """Return the user_id to scope an MCP tool call to.

    Per plan ``mcp_token_user_map`` D8 the canonical resolution order is:

    1. ``_current_mcp_user_id`` ContextVar — set by ``auth.auth_middleware``
       when ``_check_mcp_bearer`` already verified an ``lmcp_…`` token.
       This is the SINGLE-VERIFY cache: the DB lookup happens exactly once
       per request, in the middleware, and is reused here. Re-calling
       ``services.mcp_tokens.verify`` from this resolver is forbidden.
    2. Lumogis JWT ``sub`` (if a valid JWT bearer is present) — the
       multi-user path that does not use a per-user MCP token.
    3. ``MCP_DEFAULT_USER_ID`` env var — legacy single-user / shared
       ``MCP_AUTH_TOKEN`` fallback.
    4. Otherwise raise ``RuntimeError`` so tools fail loudly instead of
       silently writing into a ``"default"`` bucket.
    """
    cached_user_id = _current_mcp_user_id.get()
    if cached_user_id:
        return cached_user_id

    bearer = _current_bearer_token()
    if bearer:
        try:
            from auth import verify_token  # local import: keep MCP boot light

            payload = verify_token(bearer)
            if payload and payload.get("sub"):
                return str(payload["sub"])
        except Exception:
            _log.debug("MCP: JWT verify failed, falling back to MCP_DEFAULT_USER_ID")

    fallback = os.environ.get("MCP_DEFAULT_USER_ID", "").strip()
    if fallback:
        return fallback

    raise RuntimeError(
        "MCP tool called without a resolvable user_id. Set MCP_DEFAULT_USER_ID "
        "(self-hosted single-user) or ensure clients present a Lumogis JWT."
    )


_current_bearer: ContextVar[str | None] = ContextVar("lumogis_mcp_bearer", default=None)
# Plan D8 — populated by ``auth.auth_middleware`` from the
# ``lmcp_…`` verify result that ``_check_mcp_bearer`` already produced.
# Reading these ContextVars MUST NOT trigger a second DB lookup; the
# middleware is the ONLY writer.
_current_mcp_token_id: ContextVar[str | None] = ContextVar("lumogis_mcp_token_id", default=None)
_current_mcp_user_id: ContextVar[str | None] = ContextVar("lumogis_mcp_user_id", default=None)
# LUM-291 — per-request token scopes. None = unrestricted (JWT/legacy/unscoped
# tokens); a non-empty list = allowlist; [] = no access.
_current_mcp_scopes: ContextVar[list[str] | None] = ContextVar(
    "lumogis_mcp_scopes", default=None
)


def _current_bearer_token() -> str | None:
    """Return the inbound request's Bearer token, if FastMCP exposes one.

    FastMCP does not expose request headers through the ``@tool``-decorated
    handler signature, so we keep a context-local that the MCP middleware
    populates per request. Unit tests calling tools as plain functions
    leave this as ``None`` and ``_resolve_user_id`` falls back to the env var.
    """
    return _current_bearer.get()


def _set_current_bearer(token: str | None):
    """Set the per-request Bearer token; returns the reset token."""
    return _current_bearer.set(token)


def _reset_current_bearer(reset_token) -> None:
    _current_bearer.reset(reset_token)


def _set_current_mcp_token_id(token_id: str | None):
    """Per-request setter for the verified ``lmcp_…`` token id (D8)."""
    return _current_mcp_token_id.set(token_id)


def _reset_current_mcp_token_id(reset_token) -> None:
    _current_mcp_token_id.reset(reset_token)


def _set_current_mcp_user_id(user_id: str | None):
    """Per-request setter for the verified ``lmcp_…`` user id (D8)."""
    return _current_mcp_user_id.set(user_id)


def _reset_current_mcp_user_id(reset_token) -> None:
    _current_mcp_user_id.reset(reset_token)


def _set_current_mcp_scopes(scopes: list[str] | None):
    """Per-request setter for the verified token's scopes (LUM-291)."""
    return _current_mcp_scopes.set(scopes)


def _reset_current_mcp_scopes(reset_token) -> None:
    _current_mcp_scopes.reset(reset_token)


def _resolve_scopes() -> list[str] | None:
    """Return the inbound token's scopes; ``None`` = unrestricted."""
    return _current_mcp_scopes.get()


class McpScopeError(Exception):
    """Raised by a write tool when the inbound token lacks the required scope.

    FastMCP maps a raised exception to a JSON-RPC tool error, so this surfaces
    to the client as a structured error rather than a silent success.
    """


def _require_scope(scope: str) -> None:
    """Enforce ``scope`` on the inbound MCP token (LUM-291).

    ``None`` scopes = unrestricted (every existing token, plus JWT bearers) →
    allowed. A non-None list must contain ``scope`` or the call is rejected.
    """
    scopes = _resolve_scopes()
    if scopes is not None and scope not in scopes:
        raise McpScopeError(f"missing required scope {scope!r}")


try:
    from mcp.server.fastmcp import FastMCP as _FastMCP
    from mcp.types import ToolAnnotations as _ToolAnnotations
except ImportError:
    _FastMCP = None
    _ToolAnnotations = None
    _log.warning(
        "mcp package not installed — MCP server surface disabled. "
        "Install `mcp>=1.27.2,<2` to enable /mcp."
    )


# ---------------------------------------------------------------------------
# Manifest tool schemas (hand-coded — single source of truth for both the
# /capabilities self-manifest and any future external introspection). We
# deliberately do NOT introspect FastMCP's runtime tool registry to build
# the manifest, because that would couple the public ecosystem contract to
# Pydantic's auto-generated schema titles ("memory_searchArguments" etc.)
# and silently change shape across SDK versions.
# ---------------------------------------------------------------------------

_SESSION_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "session_id": {"type": "string"},
        "summary": {"type": "string"},
        "topics": {"type": "array", "items": {"type": "string"}},
        "entities": {"type": "array", "items": {"type": "string"}},
        "score": {"type": "number", "description": "Semantic match score (0..1)."},
        "scope": {
            "type": "string",
            "enum": ["personal", "shared", "system"],
            "description": (
                "Visibility scope: 'personal' (owner-only), 'shared' "
                "(household-visible projection), or 'system' (org-wide)."
            ),
        },
    },
    "required": ["session_id", "summary", "scope"],
}

_ENTITY_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "entity_type": {"type": "string"},
        "mention_count": {"type": "integer"},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "context_tags": {"type": "array", "items": {"type": "string"}},
        "scope": {
            "type": "string",
            "enum": ["personal", "shared", "system"],
            "description": (
                "Visibility scope: 'personal' (owner-only), 'shared' "
                "(household-visible projection), or 'system' (org-wide)."
            ),
        },
    },
    "required": ["name", "entity_type", "scope"],
}

_RECALLED_MEMORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "content": {"type": "string"},
        "entity_ids": {"type": "array", "items": {"type": "string"}},
        "valid_from": {"type": "string", "format": "date-time"},
        "valid_until": {
            "oneOf": [{"type": "string", "format": "date-time"}, {"type": "null"}],
        },
        "score": {"type": "number"},
        "source_strategies": {
            "type": "array",
            "items": {"type": "string", "enum": ["semantic", "bm25", "graph", "temporal"]},
        },
    },
    "required": ["id", "content", "valid_from", "score", "source_strategies"],
}

MCP_TOOLS_FOR_MANIFEST: list[CapabilityTool] = [
    CapabilityTool(
        name="memory.search",
        description="Semantic search across past Lumogis session summaries.",
        license_mode=CapabilityLicenseMode.COMMUNITY,
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "default": 5},
            },
            "required": ["query"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "results": {"type": "array", "items": _SESSION_SUMMARY_SCHEMA},
            },
            "required": ["results"],
        },
    ),
    CapabilityTool(
        name="memory.get_recent",
        description="Return the most recent Lumogis session summaries (chronological).",
        license_mode=CapabilityLicenseMode.COMMUNITY,
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "default": 10},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "sessions": {"type": "array", "items": _SESSION_SUMMARY_SCHEMA},
            },
            "required": ["sessions"],
        },
    ),
    CapabilityTool(
        name="entity.lookup",
        description="Find an entity by exact name (case-insensitive).",
        license_mode=CapabilityLicenseMode.COMMUNITY,
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "entity": {
                    "oneOf": [_ENTITY_SUMMARY_SCHEMA, {"type": "null"}],
                },
            },
            "required": ["entity"],
        },
    ),
    CapabilityTool(
        name="entity.search",
        description="Search entities by partial name (substring, case-insensitive).",
        license_mode=CapabilityLicenseMode.COMMUNITY,
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "default": 10},
            },
            "required": ["query"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "entities": {"type": "array", "items": _ENTITY_SUMMARY_SCHEMA},
            },
            "required": ["entities"],
        },
    ),
    CapabilityTool(
        name="context.build",
        description=(
            "Assemble relevant context for a query by combining semantic "
            "document search and past session memory, capped at max_tokens."
        ),
        license_mode=CapabilityLicenseMode.COMMUNITY,
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_tokens": {"type": "integer", "minimum": 100, "default": 2000},
            },
            "required": ["query"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "context": {"type": "string"},
                "sources": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["context", "sources"],
        },
    ),
    CapabilityTool(
        name="recall",
        description=(
            "Fused memory recall (semantic + BM25 + graph + temporal) over the "
            "Lumogis memory store, with optional cross-encoder rerank."
        ),
        license_mode=CapabilityLicenseMode.COMMUNITY,
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language recall query."},
                "bank": {
                    "type": "string",
                    "default": "coding",
                    "description": (
                        "Memory bank / context. Use bank=\"*\" to search across all "
                        "banks for the same user_id (read-only opt-in)."
                    ),
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                "retrieval_strategies": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["semantic", "bm25", "graph", "temporal"]},
                    "default": ["semantic", "bm25", "graph", "temporal"],
                },
                "as_of": {
                    "type": "string",
                    "format": "date-time",
                    "description": "Recall memories valid at this instant (default: now).",
                },
                "rerank": {"type": "boolean", "default": True},
            },
            "required": ["query"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "memories": {"type": "array", "items": _RECALLED_MEMORY_SCHEMA},
            },
            "required": ["memories"],
        },
    ),
]


def build_core_manifest() -> CapabilityManifest:
    """Return the CapabilityManifest describing Lumogis Core itself.

    Used by GET /capabilities so external systems can discover Core via the
    same contract that Area 1 defined for out-of-process services.

    NOTE: Core never registers itself in its own CapabilityRegistry — the
    registry is for *out-of-process* services only. This manifest exists
    purely for external discovery (Thunderbolt, MCP clients, future
    capability marketplaces).
    """
    return CapabilityManifest(
        name="lumogis-core",
        id="lumogis.core",
        version=CORE_VERSION,
        type="service",
        transport=CapabilityTransport.MCP,
        license_mode=CapabilityLicenseMode.COMMUNITY,
        maturity=CapabilityMaturity.PREVIEW,
        description=(
            "Lumogis Core — open-source self-hosted personal AI control "
            "plane. Exposes community-tier memory, entity, and context "
            "tools over MCP."
        ),
        tools=list(MCP_TOOLS_FOR_MANIFEST),
        health_endpoint="/health",
        capabilities_endpoint="/capabilities",
        permissions_required=[],
        config_schema={"type": "object", "properties": {}},
        min_core_version=CORE_VERSION,
        maintainer="Lumogis",
    )


# ---------------------------------------------------------------------------
# Tool implementations — each is a thin wrapper over an existing service
# helper. Defined at module scope so they can be unit-tested directly
# (without a running MCP transport) and so that build_fastmcp() can
# register them on a fresh FastMCP each lifespan startup.
# ---------------------------------------------------------------------------


def memory_search(query: str, limit: int = 5) -> dict:
    """MCP tool: memory.search — semantic search across past sessions."""
    from services.memory import retrieve_context

    hits = retrieve_context(query=query, limit=limit, user_id=_resolve_user_id())
    return {
        "results": [
            {
                "session_id": h.session_id,
                "summary": h.summary,
                "score": h.score,
                "scope": getattr(h, "scope", "personal"),
            }
            for h in hits
        ],
    }


def memory_get_recent(limit: int = 10) -> dict:
    """MCP tool: memory.get_recent — most recent session summaries."""
    from services.memory import recent_sessions

    sessions = recent_sessions(limit=limit, user_id=_resolve_user_id())
    return {
        "sessions": [
            {
                "session_id": s.session_id,
                "summary": s.summary,
                "topics": s.topics,
                "entities": s.entities,
                "scope": getattr(s, "scope", "personal"),
            }
            for s in sessions
        ],
    }


def entity_lookup(name: str) -> dict:
    """MCP tool: entity.lookup — exact case-insensitive name match."""
    from services.entities import lookup_by_name

    return {"entity": lookup_by_name(name=name, user_id=_resolve_user_id())}


def entity_search(query: str, limit: int = 10) -> dict:
    """MCP tool: entity.search — partial case-insensitive name search."""
    from services.entities import search_by_name

    return {
        "entities": search_by_name(query=query, limit=limit, user_id=_resolve_user_id()),
    }


def context_build(query: str, max_tokens: int = 2000) -> dict:
    """MCP tool: context.build — combine document + memory hits, budget-capped.

    Intentionally simple — the premium context.build_pack tool (graph-aware,
    provenance-tracked) will live in the future KG capability service.
    Failures in either underlying source are swallowed so the tool always
    returns a usable shape; partial results are better than a hard error
    for downstream MCP clients.
    """
    from services.context_budget import truncate_text
    from services.memory import retrieve_context
    from services.search import semantic_search

    user_id = _resolve_user_id()
    try:
        doc_hits = semantic_search(query=query, limit=5, user_id=user_id)
    except Exception as exc:
        _log.warning("context.build: semantic_search failed — %s", exc)
        doc_hits = []
    try:
        mem_hits = retrieve_context(query=query, limit=3, user_id=user_id)
    except Exception as exc:
        _log.warning("context.build: retrieve_context failed — %s", exc)
        mem_hits = []

    chunks: list[str] = []
    sources: list[str] = []
    seen_sources: set[str] = set()

    for hit in doc_hits:
        text = getattr(hit, "text", None) or getattr(hit, "summary", None) or ""
        if text:
            chunks.append(text)
        src = getattr(hit, "source", None) or getattr(hit, "document_id", None)
        if src and src not in seen_sources:
            sources.append(str(src))
            seen_sources.add(str(src))

    for hit in mem_hits:
        if hit.summary:
            chunks.append(f"[session {hit.session_id}] {hit.summary}")
        tag = f"session:{hit.session_id}"
        if tag not in seen_sources:
            sources.append(tag)
            seen_sources.add(tag)

    joined = "\n\n".join(chunks)
    return {
        "context": truncate_text(joined, max_tokens=max_tokens),
        "sources": sources,
    }


def recall_tool(
    query: str,
    bank: str = "coding",
    limit: int = 10,
    retrieval_strategies: list[str] | None = None,
    as_of: str | None = None,
    rerank: bool = True,
) -> dict:
    """MCP tool: recall — fused semantic + BM25 + graph + temporal retrieval (LUM-295).

    A read tool — never gates on scope (reads are ungated). Returns memories
    valid at ``as_of`` (default now), so archived/superseded memories (LUM-526)
    are excluded. Each result carries ``source_strategies`` for observability.
    """
    from datetime import datetime, timezone

    from services.recall import recall as recall_service

    parsed_as_of: datetime | None = None
    if as_of:
        try:
            parsed_as_of = datetime.fromisoformat(as_of)
        except ValueError as exc:
            # Read tool: surface a clear client-facing error rather than an
            # opaque traceback (VERIFY-PLAN: P2 — guard malformed as_of).
            raise ValueError(
                f"recall: invalid as_of timestamp {as_of!r}; expected ISO-8601"
            ) from exc
        if parsed_as_of.tzinfo is None:
            # memories.valid_until is TIMESTAMPTZ; a naive datetime would be
            # compared as server-local time. Force UTC (VERIFY-PLAN: P3).
            parsed_as_of = parsed_as_of.replace(tzinfo=timezone.utc)

    memories = recall_service(
        user_id=_resolve_user_id(),
        bank=bank,
        query=query,
        limit=limit,
        retrieval_strategies=retrieval_strategies or ["semantic", "bm25", "graph", "temporal"],
        as_of=parsed_as_of,
        rerank=rerank,
    )
    return {"memories": [m.model_dump(mode="json") for m in memories]}


# ---------------------------------------------------------------------------
# FastMCP factory + module-level singleton. We rebuild a fresh FastMCP on
# every lifespan startup (see main.py) for two reasons:
#
#  1. StreamableHTTPSessionManager.run() can only be called ONCE per
#     FastMCP instance; reusing the singleton across lifespan restarts
#     (TestClient does this naturally) would raise on the second start.
#  2. Recreating the instance is cheap (~1ms) and gives us deterministic
#     per-process state, which matters for tests.
#
# main.py mounts the resulting Starlette sub-app once at /mcp via
# `app.mount`, then on each lifespan startup swaps the mount's inner
# `route.app` to point at the freshly-built sub-app.
# ---------------------------------------------------------------------------


def _read_only_annotations(title: str) -> Any:
    """MCP tool annotations for a read-only, side-effect-free Lumogis tool.

    All read community tools query the local Lumogis instance and never
    mutate state, so MCP clients (Cursor, Claude Desktop) can auto-approve
    them instead of prompting on every call (MCP spec 2025-03-26 tool
    annotations; LUM-290 / LUM-297).

    ``openWorldHint`` is ``False``: these tools read the operator's own
    closed memory / entity store, not an open external world (web, email).
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


def _write_annotations(title: str, *, idempotent: bool = False) -> Any:
    """MCP tool annotations for Lumogis write tools (LUM-290 + LUM-291 / LUM-526).

    ``forget`` is a reversible soft archive — ``destructiveHint=False``.
    """
    if _ToolAnnotations is None:
        return None
    return _ToolAnnotations(
        title=title,
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=idempotent,
        openWorldHint=False,
    )


def add_memory_tool(
    content: str,
    bank: str | None = None,
    tags: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    """Persist a memory + extract entities/relations (LUM-291). Requires mcp:write."""
    from models.mcp_write import AddMemoryInput
    from services import mcp_write

    _require_scope("mcp:write")
    params = AddMemoryInput(
        content=content, bank=bank or mcp_write.default_bank(), tags=tags, metadata=metadata
    )
    return mcp_write.add_memory(
        user_id=_resolve_user_id(),
        bank=params.bank,
        content=params.content,
        tags=params.tags,
        metadata=params.metadata,
    )


def add_entity_tool(
    name: str,
    entity_type: str,
    bank: str | None = None,
    aliases: list[str] | None = None,
    context_tags: list[str] | None = None,
) -> dict:
    """Create a single explicit entity (LUM-291). Requires mcp:write."""
    from models.mcp_write import AddEntityInput
    from services import mcp_write

    _require_scope("mcp:write")
    params = AddEntityInput(
        name=name,
        entity_type=entity_type,
        bank=bank or mcp_write.default_bank(),
        aliases=aliases,
        context_tags=context_tags,
    )
    return mcp_write.add_entity(
        user_id=_resolve_user_id(),
        bank=params.bank,
        name=params.name,
        entity_type=params.entity_type,
        aliases=params.aliases,
        context_tags=params.context_tags,
    )


def add_relation_tool(
    src: str,
    dst: str,
    relation_type: str,
    bank: str | None = None,
) -> dict:
    """Create a typed directed relation between two entities (LUM-291). Requires mcp:write."""
    from models.mcp_write import AddRelationInput
    from services import mcp_write

    _require_scope("mcp:write")
    params = AddRelationInput(
        src=src, dst=dst, relation_type=relation_type, bank=bank or mcp_write.default_bank()
    )
    return mcp_write.add_relation(
        user_id=_resolve_user_id(),
        bank=params.bank,
        src=params.src,
        dst=params.dst,
        relation_type=params.relation_type,
    )


def forget_tool(memory_id: str) -> dict:
    """Soft-archive a memory + its edges (LUM-526). Reversible. Requires mcp:write."""
    from models.mcp_write import ForgetInput
    from services import mcp_write

    _require_scope("mcp:write")
    params = ForgetInput(memory_id=memory_id)
    return mcp_write.forget(user_id=_resolve_user_id(), memory_id=params.memory_id)


def update_observation_tool(
    memory_id: str,
    content: str,
    tags: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    """Supersede a memory (archive old, add new) (LUM-526). Requires mcp:write."""
    from models.mcp_write import UpdateObservationInput
    from services import mcp_write

    _require_scope("mcp:write")
    params = UpdateObservationInput(
        memory_id=memory_id, content=content, tags=tags, metadata=metadata
    )
    return mcp_write.update_observation(
        user_id=_resolve_user_id(),
        memory_id=params.memory_id,
        content=params.content,
        tags=params.tags,
        metadata=params.metadata,
    )


def checkpoint_tool(summary: str, bank: str | None = None) -> dict:
    """Write a session-boundary marker memory (kind=checkpoint) (LUM-526). Requires mcp:write."""
    from models.mcp_write import CheckpointInput
    from services import mcp_write

    _require_scope("mcp:write")
    params = CheckpointInput(summary=summary, bank=bank or mcp_write.default_bank())
    return mcp_write.checkpoint(
        user_id=_resolve_user_id(), bank=params.bank, summary=params.summary
    )


def build_fastmcp() -> Any:
    """Construct a fresh FastMCP server with community read + write tools.

    Returns the FastMCP instance. The caller is responsible for calling
    `.streamable_http_app()` on it (which lazily creates the session
    manager) and entering `mcp.session_manager.run()` to start the
    underlying anyio task group.
    """
    if _FastMCP is None:
        return None

    fresh = _FastMCP(
        name="lumogis-core",
        instructions=(
            "Lumogis community memory and entity tools. Read tools "
            "(memory.*, entity.*, context.build) plus write tools "
            "(add_memory, add_entity, add_relation, forget, update_observation, "
            "checkpoint) that require an mcp:write-scoped token. forget is a "
            "reversible soft archive, not a hard delete. Stateless; single-user "
            "local by default."
        ),
        stateless_http=True,
        json_response=True,
    )
    # Make the public path exactly /mcp when mounted at /mcp in main.py.
    # Without this override the Starlette sub-app keeps its default /mcp
    # internal route, producing /mcp/mcp and a 307→404 redirect chain.
    fresh.settings.streamable_http_path = "/"

    fresh.tool(
        name="memory.search",
        description="Semantic search across past Lumogis session summaries.",
        annotations=_read_only_annotations("Search memory"),
    )(memory_search)
    fresh.tool(
        name="memory.get_recent",
        description="Return the most recent Lumogis session summaries (chronological).",
        annotations=_read_only_annotations("Recent sessions"),
    )(memory_get_recent)
    fresh.tool(
        name="entity.lookup",
        description="Find an entity by exact name (case-insensitive).",
        annotations=_read_only_annotations("Look up entity"),
    )(entity_lookup)
    fresh.tool(
        name="entity.search",
        description="Search entities by partial name (substring, case-insensitive).",
        annotations=_read_only_annotations("Search entities"),
    )(entity_search)
    fresh.tool(
        name="context.build",
        description=(
            "Assemble relevant context for a query by combining semantic "
            "document search and past session memory, capped at max_tokens."
        ),
        annotations=_read_only_annotations("Build context"),
    )(context_build)
    fresh.tool(
        name="recall",
        description=(
            "Fused memory recall (semantic + BM25 + graph + temporal) with "
            "optional cross-encoder rerank. Returns memories valid at as_of "
            "(default now); archived/superseded memories are excluded."
        ),
        annotations=_read_only_annotations("Recall memories"),
    )(recall_tool)
    fresh.tool(
        name="add_memory",
        description=(
            "Persist a memory into the Lumogis knowledge graph (extracts "
            "entities + relations). Requires an mcp:write-scoped token."
        ),
        annotations=_write_annotations("Add memory"),
    )(add_memory_tool)
    fresh.tool(
        name="add_entity",
        description=(
            "Create a single explicit entity. entity_type must be one of the "
            "registered types: PERSON, ORG, PROJECT, CONCEPT, or the coding "
            "types CODING_DECISION, CODING_CONVENTION, COMPONENT, FAILURE, "
            "SESSION, TASK, LIBRARY. Requires an mcp:write-scoped token."
        ),
        annotations=_write_annotations("Add entity"),
    )(add_entity_tool)
    fresh.tool(
        name="add_relation",
        description=(
            "Create a typed directed relation between two entities. "
            "relation_type must be one of the registered types: DEPENDS_ON, "
            "PART_OF, DECIDED, RELATES_TO, SUPERSEDES, DECIDED_BY, IMPLEMENTS, "
            "REPLACES, CAUSED_BY, DISCUSSED_IN_SESSION, BLOCKED_BY, "
            "REFERENCES_ISSUE. Requires an mcp:write-scoped token."
        ),
        annotations=_write_annotations("Add relation", idempotent=True),
    )(add_relation_tool)
    # LUM-526 — supersede/archive surface. forget is a reversible soft archive
    # (not a hard delete); all require mcp:write.
    fresh.tool(
        name="forget",
        description=(
            "Archive a memory by id (reversible soft archive — sets it inactive; "
            "not a hard delete). Requires an mcp:write-scoped token."
        ),
        annotations=_write_annotations("Forget memory", idempotent=True),
    )(forget_tool)
    fresh.tool(
        name="update_observation",
        description=(
            "Supersede a memory: archive the old one and store new content in its "
            "place (history retained). Requires an mcp:write-scoped token."
        ),
        annotations=_write_annotations("Update observation"),
    )(update_observation_tool)
    fresh.tool(
        name="checkpoint",
        description=(
            "Record a session-boundary checkpoint: a memory holding a summary, "
            "marked with metadata.kind=checkpoint. Requires an mcp:write-scoped token."
        ),
        annotations=_write_annotations("Checkpoint"),
    )(checkpoint_tool)
    return fresh


# Module-level singleton — built once at import so callers (main.py,
# routes/capabilities.py, the dashboard status endpoint) can check
# `mcp is None` to detect SDK absence without invoking the factory.
# Replaced in-place by main.py's lifespan on each startup.
mcp: Any = build_fastmcp()
