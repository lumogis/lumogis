# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Read-only graph query helpers for M3.

Public surface
--------------
  resolve_entity_by_name  — Postgres entity lookup (name + alias, deterministic)
  ego_network             — direct RELATES_TO neighbors above co-occurrence threshold
  shortest_path           — shortestPath between two entity nodes
  mention_sources         — information objects (docs/sessions/notes) that MENTION an entity
  query_graph_tool        — ToolSpec handler; returns a JSON string
  on_context_building     — CONTEXT_BUILDING hook handler; appends graph context
  select_context_entities — ranked entity rows before ego expansion (LUM-98 seam)

Architecture rules (M3)
------------------------
- All graph reads are routed through the existing GraphStore.query() protocol.
- No Cypher is exposed to the LLM. Modes are "ego", "path", "mentions".
- Entity resolution uses deterministic Postgres lookup; CONTEXT_BUILDING may add an
  optional Qdrant semantic pass when ``LUMOGIS_CONTEXT_BUILDER_SEMANTIC=true``.
- If the graph backend is unavailable, all functions return gracefully (no exceptions).
- CONTEXT_BUILDING receives ``user_id`` from Core chat or KG ``POST /context``; handlers
  default to ``"default"`` when omitted. Semantic search uses ``visible_qdrant_filter``
  on the ``entities`` collection (payloads today are personal ``scope`` per upsert).

Log policy
----------
Log IDs, counts, durations, and resolution success/failure.
Do NOT log entity names (PII risk), raw query text, or user content.
"""

import json
import logging
import math
import re
import time
from datetime import datetime
from datetime import timezone

from auth import UserContext
from visibility import visible_cypher_fragment
from visibility import visible_filter
from visibility import visible_qdrant_filter

import config
from graph.memory_types import MEMORY_TYPE_CORRECTION
from graph.memory_types import MEMORY_TYPE_RELATIONSHIP
from graph.memory_types import MEMORY_TYPE_USER_PREFERENCE
from graph.temporal import ensure_utc
from graph.temporal import temporal_feature_enabled
from graph.temporal import to_graph_ts

_log = logging.getLogger(__name__)

# Hard bounds to prevent runaway queries.
_MAX_DEPTH = 4
_MAX_LIMIT = 20
_DEFAULT_LIMIT = 10

# Soft wall-clock budget for CONTEXT_BUILDING Phase B (embed + Qdrant) in-process.
_CONTEXT_SEMANTIC_PHASE_B_BUDGET_S = 0.035


# ---------------------------------------------------------------------------
# Entity resolution (Postgres — deterministic, no LLM)
# ---------------------------------------------------------------------------


def resolve_entity_by_name(name: str, user_id: str) -> dict | None:
    """Return a Postgres entity row matching name or alias (case-insensitive).

    Searches canonical name first, then each alias value. Returns the row
    with the highest mention_count when multiple candidates share the same
    normalized name. Returns None if no match is found.

    Visibility: applies :func:`visibility.visible_filter` so the resolved
    entity may be the caller's personal row OR a shared/system row in the
    household. The graph reads that follow (``ego_network`` /
    ``mention_sources`` / ``shortest_path``) apply the Cypher
    counterpart, so cross-user-personal data never bleeds in.

    This is the only entity resolution path used by M3 — no fuzzy/semantic
    fallback is added here. Fuzzy search remains in query_entity (tools.py).
    """
    ms = config.get_metadata_store()
    where_clause, where_params = visible_filter(UserContext(user_id=user_id))
    return ms.fetch_one(
        "SELECT entity_id, name, entity_type, aliases, context_tags, mention_count, scope, "
        "       published_from "
        "FROM entities "
        f"WHERE {where_clause} AND ("
        "  lower(name) = lower(%s) "
        "  OR lower(%s) = ANY(SELECT lower(a) FROM unnest(aliases) a)"
        ") "
        "AND (is_staged IS NOT TRUE) "
        "ORDER BY mention_count DESC "
        "LIMIT 1",
        (*where_params, name, name),
    )


# ---------------------------------------------------------------------------
# Graph traversal helpers (FalkorDB via GraphStore protocol)
# ---------------------------------------------------------------------------


def _temporal_validity_fragment(as_of: datetime | None = None) -> tuple[str, dict]:
    """LUM-104 read-side validity filter — flag-gated, null-tolerant.

    Flag off → empty fragment (queries stay byte-identical to today, pinned
    by test). Flag on, ``as_of`` None → currently-valid semantics
    (``IS NULL``; FalkorDB property absence satisfies it, so un-stamped
    legacy edges pass). ``as_of`` set → point-in-time form (internal only —
    not surfaced on any route in v1; route exposure is a deferred revisit
    note in the plan's follow-up register).
    """
    if not temporal_feature_enabled():
        return "", {}
    if as_of is None:
        return "  AND (r.invalid_at IS NULL) AND (r.expired_at IS NULL) ", {}
    ts = to_graph_ts(ensure_utc(as_of))
    # Point-in-time needs all three axes: not yet invalidated/expired AT the
    # requested time AND already valid by then (null-tolerant on valid_at so
    # un-stamped legacy edges still pass — verify-plan fix: without the
    # valid_at bound, a 2024 fact would report as valid in a 2020 as-of read).
    return (
        "  AND (r.invalid_at IS NULL OR r.invalid_at > $temporal_as_of) "
        "AND (r.expired_at IS NULL OR r.expired_at > $temporal_as_of) "
        "AND (r.valid_at IS NULL OR r.valid_at <= $temporal_as_of) ",
        {"temporal_as_of": ts},
    )


def ego_network(
    gs,
    entity_id: str,
    user_id: str,
    depth: int = 1,
    limit: int = _DEFAULT_LIMIT,
    as_of: datetime | None = None,
) -> dict:
    """Return direct RELATES_TO neighbors of an entity (depth=1).

    Only edges with co_occurrence_count >= ``config.get_cooccurrence_threshold()``
    are returned,
    ordered by co_occurrence_count descending. The `depth` parameter is accepted
    for API symmetry but is capped at 1 for this implementation — deeper traversal
    produces too many results without additional ranking heuristics.

    Returns:
        {"entity_id": str, "edges": list[dict], "depth": int, "duration_ms": int}
        "edges" items: {"neighbor_id", "neighbor_name", "neighbor_type", "strength"}
    """
    limit = min(max(int(limit), 1), _MAX_LIMIT)
    t0 = time.monotonic()

    # RELATES_TO is stored directionally (lower→higher lumogis_id), so match
    # both directions with an undirected pattern to surface all neighbors.
    # Transition predicate: handles NULL edge_quality before the weekly scoring job runs.
    #   - NULL edge_quality: fall back to co_occurrence_count gate only (pre-Pass-3 rows)
    #   - scored edges: require edge_quality >= graph_edge_quality_threshold
    #     AND co_occurrence_count >= cooccurrence_threshold (structural sanity gate)
    cooc_threshold = config.get_cooccurrence_threshold()
    eq_threshold = config.get_graph_edge_quality_threshold()
    user = UserContext(user_id=user_id)
    center_vis, center_params = visible_cypher_fragment(user, alias="center")
    neighbor_vis, _ = visible_cypher_fragment(user, alias="neighbor")
    temporal_frag, temporal_params = _temporal_validity_fragment(as_of)
    cypher = (
        f"MATCH (center)-[r:RELATES_TO]-(neighbor) "
        f"WHERE center.lumogis_id = $eid "
        f"  AND {center_vis} "
        f"  AND {neighbor_vis} "
        f"  AND r.co_occurrence_count >= {cooc_threshold} "
        f"  AND (r.edge_quality IS NULL "
        f"       OR r.edge_quality >= {eq_threshold}) "
        f"{temporal_frag}"
        f"RETURN neighbor.lumogis_id AS neighbor_id, "
        f"       neighbor.name AS neighbor_name, "
        f"       neighbor.entity_type AS neighbor_type, "
        f"       r.co_occurrence_count AS strength "
        f"ORDER BY strength DESC "
        f"LIMIT {limit}"
    )
    try:
        rows = gs.query(cypher, {"eid": entity_id, **center_params, **temporal_params})
    except Exception:
        _log.exception("ego_network: FalkorDB query failed entity_id=%s", entity_id)
        rows = []

    edges = [
        {
            "neighbor_id": r.get("neighbor_id"),
            "neighbor_name": r.get("neighbor_name"),
            "neighbor_type": r.get("neighbor_type"),
            "strength": r.get("strength"),
        }
        for r in rows
        if r.get("neighbor_id")
    ]

    duration_ms = int((time.monotonic() - t0) * 1000)
    _log.info(
        "component=graph_query mode=ego entity_id=%s edges=%d duration_ms=%d",
        entity_id,
        len(edges),
        duration_ms,
    )
    return {
        "entity_id": entity_id,
        "edges": edges,
        "depth": 1,
        "duration_ms": duration_ms,
    }


def shortest_path(
    gs,
    from_entity_id: str,
    to_entity_id: str,
    user_id: str,
    max_depth: int = 4,
) -> dict:
    """Return the shortest path (any edge type) between two entity nodes.

    Returns:
        Found:    {"found": True, "path_length": int, "node_ids": list, "node_names": list, ...}
        Not found: {"found": False, ...}
    Edges are not filtered by co_occurrence_count; all edge types are traversed.
    """
    max_depth = min(max(int(max_depth), 1), _MAX_DEPTH)
    t0 = time.monotonic()

    # FalkorDB does not support shortestPath() in MATCH and rejects the undirected
    # form in WITH/RETURN. Use the algo.SPpaths procedure with relDirection 'both'
    # so we traverse RELATES_TO / MENTIONS / DISCUSSED_IN regardless of orientation.
    # Visibility is enforced on both endpoint nodes via visible_cypher_fragment;
    # interior path nodes are NOT scope-checked because path-traversal exposing
    # an unrelated cross-user node is acceptable when the caller already
    # resolved both endpoints through resolve_entity_by_name (which is itself
    # visibility-gated). Tightening interior nodes is a follow-up.
    # LUM-104 note: the algo.SPpaths procedure call exposes no per-edge
    # predicate seam, so interior edges are likewise not validity-filtered
    # (r.invalid_at / r.expired_at) — same accepted limitation as the interior
    # scope check above; hint-layer output only (ADR-066).
    user = UserContext(user_id=user_id)
    a_vis, a_params = visible_cypher_fragment(user, alias="a")
    b_vis, _ = visible_cypher_fragment(user, alias="b")
    cypher = (
        "MATCH (a), (b) "
        f"WHERE a.lumogis_id = $from_id AND {a_vis} "
        f"  AND b.lumogis_id = $to_id   AND {b_vis} "
        "WITH a, b "
        f"CALL algo.SPpaths({{sourceNode: a, targetNode: b, "
        f"maxLen: {max_depth}, pathCount: 1, relDirection: 'both'}}) "
        "YIELD path "
        "RETURN [n IN nodes(path) | n.lumogis_id] AS node_ids, "
        "       [n IN nodes(path) | n.name] AS node_names, "
        "       length(path) AS path_length"
    )
    try:
        rows = gs.query(
            cypher,
            {"from_id": from_entity_id, "to_id": to_entity_id, **a_params},
        )
    except Exception:
        _log.exception(
            "shortest_path: FalkorDB query failed from=%s to=%s",
            from_entity_id,
            to_entity_id,
        )
        rows = []

    duration_ms = int((time.monotonic() - t0) * 1000)

    if not rows:
        _log.info(
            "component=graph_query mode=path found=false from=%s to=%s duration_ms=%d",
            from_entity_id,
            to_entity_id,
            duration_ms,
        )
        return {
            "found": False,
            "from_entity_id": from_entity_id,
            "to_entity_id": to_entity_id,
            "duration_ms": duration_ms,
        }

    row = rows[0]
    path_length = row.get("path_length", 0)
    _log.info(
        "component=graph_query mode=path found=true length=%d from=%s to=%s duration_ms=%d",
        path_length,
        from_entity_id,
        to_entity_id,
        duration_ms,
    )
    return {
        "found": True,
        "from_entity_id": from_entity_id,
        "to_entity_id": to_entity_id,
        "path_length": path_length,
        "node_ids": row.get("node_ids") or [],
        "node_names": row.get("node_names") or [],
        "duration_ms": duration_ms,
    }


def mention_sources(
    gs,
    entity_id: str,
    user_id: str,
    limit: int = _DEFAULT_LIMIT,
    as_of: datetime | None = None,
) -> dict:
    """Return information objects that have a MENTIONS edge to the given entity.

    MENTIONS direction in the schema: source -[MENTIONS]-> entity.
    Results are ordered by timestamp descending (most recent first).

    Returns:
        {"entity_id": str, "sources": list[dict], "duration_ms": int}
        "sources" items: {"source_id", "source_type", "evidence_type", "timestamp"}
    """
    limit = min(max(int(limit), 1), _MAX_LIMIT)
    t0 = time.monotonic()

    user = UserContext(user_id=user_id)
    entity_vis, entity_params = visible_cypher_fragment(user, alias="entity")
    source_vis, _ = visible_cypher_fragment(user, alias="source")
    temporal_frag, temporal_params = _temporal_validity_fragment(as_of)
    cypher = (
        f"MATCH (source)-[r:MENTIONS]->(entity) "
        f"WHERE entity.lumogis_id = $eid AND {entity_vis} "
        f"  AND {source_vis} "
        f"{temporal_frag}"
        f"RETURN source.lumogis_id AS source_id, "
        f"       source.entity_type AS source_type, "
        f"       r.evidence_type AS evidence_type, "
        f"       r.timestamp AS ts "
        f"ORDER BY ts DESC "
        f"LIMIT {limit}"
    )
    try:
        rows = gs.query(cypher, {"eid": entity_id, **entity_params, **temporal_params})
    except Exception:
        _log.exception("mention_sources: FalkorDB query failed entity_id=%s", entity_id)
        rows = []

    sources = [
        {
            "source_id": r.get("source_id"),
            "source_type": r.get("source_type"),
            "evidence_type": r.get("evidence_type"),
            "timestamp": str(r.get("ts") or ""),
        }
        for r in rows
        if r.get("source_id")
    ]

    duration_ms = int((time.monotonic() - t0) * 1000)
    _log.info(
        "component=graph_query mode=mentions entity_id=%s sources=%d duration_ms=%d",
        entity_id,
        len(sources),
        duration_ms,
    )
    return {"entity_id": entity_id, "sources": sources, "duration_ms": duration_ms}


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------


def query_graph_tool(input_: dict, *, user_id: str) -> str:
    """Handler for the query_graph tool.

    Supported modes:
      ego      — RELATES_TO neighborhood of a single entity
      path     — shortest path between two entities (any edge type)
      mentions — information sources that MENTION a given entity

    No Cypher is exposed to the LLM. All traversal is bounded.

    ``user_id`` is keyword-only and required (LUM-884): it is the real caller's
    identity, supplied by ``run_tool`` or the service route. A ``user_id`` key
    inside ``input_`` (model-controlled) is ignored.

    Returns a JSON string with structured results plus a concise "summary" field
    for the LLM to include in its response.
    """
    if not isinstance(user_id, str) or not user_id:
        raise TypeError("query_graph_tool: user_id (keyword-only) is required")
    mode = (input_.get("mode") or "").strip().lower()
    if mode not in ("ego", "path", "mentions"):
        return json.dumps({"error": f"Unknown mode '{mode}'. Supported modes: ego, path, mentions"})

    gs = config.get_graph_store()
    if gs is None:
        _log.info("component=graph_query mode=%s graph_unavailable=true", mode)
        return json.dumps(
            {
                "available": False,
                "message": (
                    "Graph backend is not configured (GRAPH_BACKEND=none). "
                    "Enable FalkorDB to use graph queries."
                ),
            }
        )

    limit = min(int(input_.get("limit") or _DEFAULT_LIMIT), _MAX_LIMIT)

    if mode == "ego":
        return _handle_ego(gs, input_, user_id, limit)
    if mode == "path":
        return _handle_path(gs, input_, user_id)
    return _handle_mentions(gs, input_, user_id, limit)


def _handle_ego(gs, input_: dict, user_id: str, limit: int) -> str:
    name = (input_.get("entity") or "").strip()
    if not name:
        return json.dumps({"error": "entity is required for mode=ego"})

    # depth is accepted for API forward-compatibility but the query is always
    # single-hop. We return depth_used=1 so callers know the actual behaviour.
    _depth_requested = int(input_.get("depth") or 1)  # stored for transparency only
    _DEPTH_USED = 1  # actual traversal depth; multi-hop deferred to a later milestone

    entity = resolve_entity_by_name(name, user_id)
    if entity is None:
        _log.info("component=graph_query mode=ego entity_resolved=false")
        return json.dumps(
            {
                "found": False,
                "mode": "ego",
                "entity": name,
                "message": "Entity not found in the knowledge graph.",
            }
        )

    result = ego_network(gs, entity["entity_id"], user_id, depth=_DEPTH_USED, limit=limit)
    edges = result["edges"]

    if not edges:
        summary = (
            f"{entity['name']} is in the graph but has no connected entities "
            f"above the co-occurrence threshold yet."
        )
    else:
        neighbor_names = [e["neighbor_name"] for e in edges[:5] if e.get("neighbor_name")]
        tail = "…" if len(edges) > len(neighbor_names) else ""
        summary = (
            f"{entity['name']} is connected to {len(edges)} entity/entities"
            + (f": {', '.join(neighbor_names)}{tail}" if neighbor_names else "")
            + "."
        )

    return json.dumps(
        {
            "mode": "ego",
            "found": True,
            "entity": entity["name"],
            "entity_id": entity["entity_id"],
            "entity_type": entity["entity_type"],
            "depth_used": _DEPTH_USED,
            "neighbors": edges,
            "summary": summary,
            "duration_ms": result["duration_ms"],
        }
    )


def _handle_path(gs, input_: dict, user_id: str) -> str:
    from_name = (input_.get("from_entity") or "").strip()
    to_name = (input_.get("to_entity") or "").strip()
    if not from_name or not to_name:
        return json.dumps({"error": "from_entity and to_entity are required for mode=path"})

    max_depth = min(int(input_.get("max_depth") or 4), _MAX_DEPTH)

    from_entity = resolve_entity_by_name(from_name, user_id)
    to_entity = resolve_entity_by_name(to_name, user_id)

    if from_entity is None:
        _log.info("component=graph_query mode=path from_resolved=false")
        return json.dumps(
            {
                "found": False,
                "mode": "path",
                "from_entity": from_name,
                "to_entity": to_name,
                "message": f"'{from_name}' not found in the knowledge graph.",
            }
        )
    if to_entity is None:
        _log.info("component=graph_query mode=path to_resolved=false")
        return json.dumps(
            {
                "found": False,
                "mode": "path",
                "from_entity": from_name,
                "to_entity": to_name,
                "message": f"'{to_name}' not found in the knowledge graph.",
            }
        )
    if from_entity["entity_id"] == to_entity["entity_id"]:
        return json.dumps(
            {
                "found": True,
                "mode": "path",
                "from_entity": from_entity["name"],
                "to_entity": to_entity["name"],
                "path_length": 0,
                "path": [from_entity["name"]],
                "summary": (
                    f"{from_entity['name']} and {to_entity['name']} resolve to the same entity."
                ),
                "duration_ms": 0,
            }
        )

    result = shortest_path(
        gs,
        from_entity["entity_id"],
        to_entity["entity_id"],
        user_id,
        max_depth=max_depth,
    )

    if not result["found"]:
        return json.dumps(
            {
                "found": False,
                "mode": "path",
                "from_entity": from_entity["name"],
                "to_entity": to_entity["name"],
                "max_depth_searched": max_depth,
                "summary": (
                    f"No connection found between {from_entity['name']} and "
                    f"{to_entity['name']} within {max_depth} hops."
                ),
                "duration_ms": result["duration_ms"],
            }
        )

    node_names = result.get("node_names") or []
    path_str = (
        " → ".join(str(n) for n in node_names)
        if node_names
        else f"{from_entity['name']} → {to_entity['name']}"
    )
    return json.dumps(
        {
            "found": True,
            "mode": "path",
            "from_entity": from_entity["name"],
            "to_entity": to_entity["name"],
            "path_length": result["path_length"],
            "path": node_names,
            "node_ids": result.get("node_ids") or [],
            "summary": (
                f"{from_entity['name']} connects to {to_entity['name']} "
                f"in {result['path_length']} hop(s): {path_str}."
            ),
            "duration_ms": result["duration_ms"],
        }
    )


def _handle_mentions(gs, input_: dict, user_id: str, limit: int) -> str:
    name = (input_.get("entity") or "").strip()
    if not name:
        return json.dumps({"error": "entity is required for mode=mentions"})

    entity = resolve_entity_by_name(name, user_id)
    if entity is None:
        _log.info("component=graph_query mode=mentions entity_resolved=false")
        return json.dumps(
            {
                "found": False,
                "mode": "mentions",
                "entity": name,
                "message": "Entity not found in the knowledge graph.",
            }
        )

    result = mention_sources(gs, entity["entity_id"], user_id, limit=limit)
    sources = result["sources"]

    if not sources:
        summary = f"{entity['name']} has no indexed mention sources in the graph yet."
    else:
        type_counts: dict[str, int] = {}
        for s in sources:
            t = (s.get("evidence_type") or s.get("source_type") or "unknown").lower()
            type_counts[t] = type_counts.get(t, 0) + 1
        type_summary = ", ".join(f"{count} {label}(s)" for label, count in type_counts.items())
        summary = f"{entity['name']} is mentioned in {len(sources)} source(s): {type_summary}."

    return json.dumps(
        {
            "mode": "mentions",
            "found": True,
            "entity": entity["name"],
            "entity_id": entity["entity_id"],
            "entity_type": entity["entity_type"],
            "sources": sources,
            "summary": summary,
            "duration_ms": result["duration_ms"],
        }
    )


# ---------------------------------------------------------------------------
# CONTEXT_BUILDING hook handler
# ---------------------------------------------------------------------------


def _term_in_query(term: str, query_lower: str) -> bool:
    """Return True when `term` appears in `query_lower` at a word boundary.

    Uses `re.search(r'\\b<term>\\b')` so "Ada" does NOT match inside "Canada"
    or "Cascade", while multi-word names like "Ada Lovelace" still match
    correctly (the interior space is not a word boundary character; only the
    outermost edges are checked).

    The regex is compiled inline — at 100 candidates this stays well under 1ms.
    Raises no exceptions; returns False on any regex error.
    """
    if not term:
        return False
    try:
        pattern = r"\b" + re.escape(term.lower()) + r"\b"
        return bool(re.search(pattern, query_lower))
    except re.error:
        return False


def _detect_entities_in_query(query: str, user_id: str) -> list[dict]:
    """Return entities whose name or alias appears in the query at a word boundary.

    Fetches the top 100 high-signal entities for this user (by mention_count,
    gated on ``config.get_graph_min_mention_count()``) then uses word-boundary
    regex matching. Collects **all** boundary matches (no cap during scan); the
    entity budget is applied later in :func:`select_context_entities`.
    """
    ms = config.get_metadata_store()
    where_clause, where_params = visible_filter(UserContext(user_id=user_id))
    try:
        candidates = ms.fetch_all(
            "SELECT entity_id, name, entity_type, aliases, mention_count, updated_at, "
            "memory_type, last_verified_at "
            "FROM entities "
            f"WHERE {where_clause} AND mention_count >= %s "
            "AND (is_staged IS NOT TRUE) "
            "ORDER BY mention_count DESC "
            "LIMIT 100",
            (*where_params, config.get_graph_min_mention_count()),
        )
    except Exception:
        _log.exception("context_building: entity candidate fetch failed")
        return []

    query_lower = query.lower()
    matched: list[dict] = []
    for row in candidates:
        if _term_in_query(row["name"], query_lower):
            matched.append(row)
            continue
        for alias in row.get("aliases") or []:
            if _term_in_query(alias, query_lower):
                matched.append(row)
                break

    _log.info(
        "component=graph_context_building candidates=%d matched=%d",
        len(candidates),
        len(matched),
    )
    return matched


def domain_boost(entity_id: str, user_id: str) -> float:
    """Seam for LUM-202 domain scores; v1 returns 0 (no API yet)."""
    del entity_id, user_id
    return 0.0


def recency_from_updated_at(updated_at) -> float:
    """Bounded decay score in (0, 1] from ``updated_at``; 0 when unknown.

    ``updated_at`` is correlated with mention activity (bumps on upsert); keep
    ``LUMOGIS_CONTEXT_ENTITY_RANK_DELTA=0`` (default) until a dedicated recency
    signal exists.
    """
    if updated_at is None:
        return 0.0
    if isinstance(updated_at, datetime):
        ts = updated_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    else:
        try:
            raw = str(updated_at).replace("Z", "+00:00")
            ts = datetime.fromisoformat(raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            return 0.0
    now = datetime.now(timezone.utc)
    age_sec = max(0.0, (now - ts).total_seconds())
    age_days = age_sec / 86400.0
    return 1.0 / (1.0 + age_days)


def _mention_log_term(mc: int) -> float:
    """Shared log(1+mentions) primitive for ranking and LUM-124 confidence (see plan D3.4)."""
    return math.log(1.0 + max(int(mc), 0))


def _mention_gain_bounded(mc: int) -> float:
    return min(1.0, _mention_log_term(mc) / math.log(51.0))


_DISPLAY_ENTITY_TYPES = frozenset({"PERSON", "ORG", "PROJECT", "CONCEPT", "FILE"})


def _strip_ascii_controls(text: str) -> str:
    return "".join(ch for ch in text if ord(ch) >= 32)


def _normalize_entity_type_display(raw: object) -> str:
    if raw is None or not isinstance(raw, str) or not raw.strip():
        return "UNKNOWN"
    u = raw.strip().upper()
    return u if u in _DISPLAY_ENTITY_TYPES else "UNKNOWN"


def _parse_ts_to_utc(val: object) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        ts = val
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    try:
        raw = str(val).replace("Z", "+00:00")
        ts = datetime.fromisoformat(raw)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    except Exception:
        return None


def _fmt_rfc3339_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hint_memory_type_literal(row: dict) -> str:
    raw = row.get("memory_type")
    if isinstance(raw, str):
        r = raw.strip().lower()
        if r == MEMORY_TYPE_CORRECTION:
            return MEMORY_TYPE_CORRECTION
        if r == MEMORY_TYPE_USER_PREFERENCE:
            return MEMORY_TYPE_USER_PREFERENCE
        if r == MEMORY_TYPE_RELATIONSHIP:
            return MEMORY_TYPE_RELATIONSHIP
    return MEMORY_TYPE_RELATIONSHIP


def _last_seen_token(row: dict) -> str:
    lv = _parse_ts_to_utc(row.get("last_verified_at"))
    if lv is not None:
        return _fmt_rfc3339_z(lv)
    up = _parse_ts_to_utc(row.get("updated_at"))
    if up is not None:
        return _fmt_rfc3339_z(up)
    return "unknown"


def _context_confidence(row: dict, *, now: datetime) -> float:
    """Scalar confidence in [0, 1] per LUM-124 plan (mention saturation × decay)."""
    anchor = _parse_ts_to_utc(row.get("last_verified_at")) or _parse_ts_to_utc(
        row.get("updated_at")
    )
    mc = int(row.get("mention_count") or 0)
    g = _mention_gain_bounded(mc)
    if anchor is None:
        raw = max(0.0, min(1.0, 0.6 * g))
        return raw
    age_sec = max(0.0, (now - anchor).total_seconds())
    age_days = age_sec / 86400.0
    half = float(config.get_entity_half_life_days(row.get("entity_type")))
    if half <= 0:
        decay = 1.0
    else:
        decay = 2.0 ** (-age_days / half)
    return max(0.0, min(1.0, g * decay))


def _graph_context_hint_suffix(row: dict, *, now: datetime) -> str:
    lit = _hint_memory_type_literal(row)
    conf = _context_confidence(row, now=now)
    tok = _last_seen_token(row)
    return f" (hint: type={lit}; confidence={conf:.2f}; last_seen={tok})"


def _final_entity_score(row: dict, user_id: str, *, explicit: bool, sem_score: float) -> float:
    alpha = config.get_context_entity_rank_alpha()
    beta = config.get_context_entity_rank_beta()
    gamma = config.get_context_entity_rank_gamma()
    delta = config.get_context_entity_rank_delta()
    bonus = config.get_context_entity_explicit_bonus() if explicit else 0.0
    mc = int(row.get("mention_count") or 0)
    mention_term = beta * _mention_log_term(mc)
    sem_norm = max(0.0, min(1.0, float(sem_score)))
    domain_term = gamma * domain_boost(row["entity_id"], user_id)
    recency_term = delta * recency_from_updated_at(row.get("updated_at"))
    return alpha * sem_norm + mention_term + domain_term + recency_term + bonus


def _fetch_entity_row_for_context(entity_id: str, user_id: str) -> dict | None:
    ms = config.get_metadata_store()
    where_clause, where_params = visible_filter(UserContext(user_id=user_id))
    try:
        return ms.fetch_one(
            "SELECT entity_id, name, entity_type, aliases, mention_count, updated_at, "
            "memory_type, last_verified_at "
            f"FROM entities WHERE {where_clause} AND entity_id = %s "
            "AND (is_staged IS NOT TRUE)",
            (*where_params, entity_id),
        )
    except Exception:
        _log.warning(
            "context_building: entity fetch failed entity_id=%s",
            entity_id,
            exc_info=True,
        )
        return None


def _rank_and_truncate_entity_map(merged: dict[str, dict], user_id: str) -> list[dict]:
    min_mc = config.get_graph_min_mention_count()
    items: list[tuple[float, str, dict]] = []
    for eid, pack in merged.items():
        row = pack["row"]
        if int(row.get("mention_count") or 0) < min_mc:
            continue
        fs = _final_entity_score(
            row,
            user_id,
            explicit=bool(pack["explicit"]),
            sem_score=float(pack.get("sem_score", 0.0)),
        )
        items.append((fs, eid, row))
    items.sort(key=lambda t: (-t[0], t[1]))
    budget = config.get_context_entity_budget()
    out: list[dict] = []
    for _, _, row in items[:budget]:
        out.append(
            {
                "entity_id": row["entity_id"],
                "name": row["name"],
                "entity_type": row["entity_type"],
                "aliases": row.get("aliases") or [],
                "mention_count": row.get("mention_count"),
                "updated_at": row.get("updated_at"),
                "memory_type": row.get("memory_type"),
                "last_verified_at": row.get("last_verified_at"),
            }
        )
    return out


def select_context_entities(query: str, user_id: str) -> list[dict]:
    """Return ranked Postgres-shaped entity rows before ego expansion.

    Merges explicit word-boundary matches with optional Qdrant semantic hits,
    dedupes by ``entity_id``, applies ``get_graph_min_mention_count()`` gating,
    scores, sorts (score desc, ``entity_id`` asc), then truncates to
    ``get_context_entity_budget()``.
    """
    explicit_rows = _detect_entities_in_query(query, user_id)
    merged: dict[str, dict] = {}
    for row in explicit_rows:
        eid_s = str(row["entity_id"])
        merged[eid_s] = {"row": dict(row), "explicit": True, "sem_score": 0.0}

    if not config.get_context_entity_semantic_enabled():
        return _rank_and_truncate_entity_map(merged, user_id)

    embedder = config.get_embedder()
    vs = config.get_vector_store()
    if embedder is None or vs is None:
        return _rank_and_truncate_entity_map(merged, user_id)

    t_b = time.monotonic()
    try:
        vector = embedder.embed(query)
    except Exception:
        _log.warning("context_building: semantic embed failed", exc_info=True)
        return _rank_and_truncate_entity_map(merged, user_id)

    if time.monotonic() - t_b > _CONTEXT_SEMANTIC_PHASE_B_BUDGET_S:
        return _rank_and_truncate_entity_map(merged, user_id)

    ctx = UserContext(user_id=user_id)
    try:
        hits = vs.search(
            collection="entities",
            vector=vector,
            limit=config.get_context_entity_semantic_topk(),
            threshold=config.get_context_entity_semantic_threshold(),
            filter=visible_qdrant_filter(ctx),
        )
    except Exception:
        _log.warning("context_building: semantic vector search failed", exc_info=True)
        return _rank_and_truncate_entity_map(merged, user_id)

    for hit in hits or []:
        if time.monotonic() - t_b > _CONTEXT_SEMANTIC_PHASE_B_BUDGET_S:
            break
        try:
            score = float(hit.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        sem_norm = max(0.0, min(1.0, score))
        payload = hit.get("payload") or {}
        eid = payload.get("entity_id")
        if not eid:
            continue
        row = _fetch_entity_row_for_context(str(eid), user_id)
        if row is None:
            continue
        eid_s = str(row["entity_id"])
        if eid_s in merged:
            pack = merged[eid_s]
            pack["sem_score"] = max(float(pack.get("sem_score", 0.0)), sem_norm)
        else:
            merged[eid_s] = {"row": dict(row), "explicit": False, "sem_score": sem_norm}

    return _rank_and_truncate_entity_map(merged, user_id)


def on_context_building(
    *, query: str, context_fragments: list, user_id: str = "default", **_kw: object
) -> None:
    """Append lightweight graph context to context_fragments (in place).

    Called synchronously from Core ``routes/chat.py`` via
    ``hooks.fire(Event.CONTEXT_BUILDING, ...)`` or from KG ``POST /context``.

    ``user_id`` scopes Postgres and Qdrant visibility; defaults to ``\"default\"``
    when omitted by third-party hook subscribers.
    """
    _ = _kw  # forward-compatible sink for extra hook kwargs
    gs = config.get_graph_store()
    if gs is None:
        return

    t0 = time.monotonic()
    uid = (user_id or "default").strip() or "default"
    now = datetime.now(timezone.utc)

    entities = select_context_entities(query, uid)
    if not entities:
        _log.debug("component=graph_context_building injection=skipped reason=no_entity_match")
        return

    max_edges = config.get_context_max_edges()
    lines: list[str] = []
    for entity in entities:
        result = ego_network(
            gs,
            entity["entity_id"],
            uid,
            depth=1,
            limit=max_edges,
        )
        edges = result["edges"]
        nm_disp = _strip_ascii_controls(str(entity.get("name") or ""))
        et_disp = _normalize_entity_type_display(entity.get("entity_type"))
        suffix = _graph_context_hint_suffix(entity, now=now)

        mem_raw = entity.get("memory_type")
        is_correction = (
            isinstance(mem_raw, str) and mem_raw.strip().lower() == MEMORY_TYPE_CORRECTION
        )

        if not edges:
            if not is_correction:
                _log.debug(
                    "component=graph_context_building entity_id=%s injection=skipped "
                    "reason=no_qualifying_edges",
                    entity["entity_id"],
                )
                continue
            lines.append(f"[Graph] {nm_disp} ({et_disp}) [no related entities].{suffix}")
            continue

        neighbor_parts = []
        for e in edges:
            n = e.get("neighbor_name")
            if not n:
                continue
            s = e.get("strength")
            neighbor_parts.append(f"{n} ({s})" if s is not None else n)
        lines.append(
            f"[Graph] {nm_disp} ({et_disp}) relates to: {', '.join(neighbor_parts)}.{suffix}"
        )

    if lines:
        context_fragments.append("\n".join(lines))
        _log.info(
            "component=graph_context_building injected=%d duration_ms=%d",
            len(lines),
            int((time.monotonic() - t0) * 1000),
        )
    else:
        _log.debug(
            "component=graph_context_building injection=skipped "
            "reason=no_qualifying_edges duration_ms=%d",
            int((time.monotonic() - t0) * 1000),
        )
