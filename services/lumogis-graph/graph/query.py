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

Architecture rules (M3)
------------------------
- All graph reads are routed through the existing GraphStore.query() protocol.
- No Cypher is exposed to the LLM. Modes are "ego", "path", "mentions".
- Entity resolution uses deterministic Postgres lookup only — no LLM, no fuzzy matching.
- If the graph backend is unavailable, all functions return gracefully (no exceptions).
- user_id is always "default" in CONTEXT_BUILDING because the hook payload does not
  carry it (Lumogis is a single-user system; "default" is always correct).

Log policy
----------
Log IDs, counts, durations, and resolution success/failure.
Do NOT log entity names (PII risk), raw query text, or user content.
"""

import json
import logging
import re
import time

import config
from auth import UserContext
from graph.schema import COOCCURRENCE_THRESHOLD
from graph.schema import MIN_MENTION_COUNT
from visibility import visible_cypher_fragment, visible_filter

_log = logging.getLogger(__name__)

# Hard bounds to prevent runaway queries.
_MAX_DEPTH = 4
_MAX_LIMIT = 20
_DEFAULT_LIMIT = 10

# Bounds for CONTEXT_BUILDING injection (per spec).
_CONTEXT_MAX_ENTITIES = 3
_CONTEXT_MAX_EDGES = 5


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

def ego_network(
    gs,
    entity_id: str,
    user_id: str,
    depth: int = 1,
    limit: int = _DEFAULT_LIMIT,
) -> dict:
    """Return direct RELATES_TO neighbors of an entity (depth=1).

    Only edges with co_occurrence_count >= COOCCURRENCE_THRESHOLD are returned,
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
    cypher = (
        f"MATCH (center)-[r:RELATES_TO]-(neighbor) "
        f"WHERE center.lumogis_id = $eid "
        f"  AND {center_vis} "
        f"  AND {neighbor_vis} "
        f"  AND r.co_occurrence_count >= {cooc_threshold} "
        f"  AND (r.edge_quality IS NULL "
        f"       OR r.edge_quality >= {eq_threshold}) "
        f"RETURN neighbor.lumogis_id AS neighbor_id, "
        f"       neighbor.name AS neighbor_name, "
        f"       neighbor.entity_type AS neighbor_type, "
        f"       r.co_occurrence_count AS strength "
        f"ORDER BY strength DESC "
        f"LIMIT {limit}"
    )
    try:
        rows = gs.query(cypher, {"eid": entity_id, **center_params})
    except Exception:
        _log.exception(
            "ego_network: FalkorDB query failed entity_id=%s", entity_id
        )
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
    cypher = (
        f"MATCH (source)-[r:MENTIONS]->(entity) "
        f"WHERE entity.lumogis_id = $eid AND {entity_vis} "
        f"  AND {source_vis} "
        f"RETURN source.lumogis_id AS source_id, "
        f"       source.entity_type AS source_type, "
        f"       r.evidence_type AS evidence_type, "
        f"       r.timestamp AS ts "
        f"ORDER BY ts DESC "
        f"LIMIT {limit}"
    )
    try:
        rows = gs.query(cypher, {"eid": entity_id, **entity_params})
    except Exception:
        _log.exception(
            "mention_sources: FalkorDB query failed entity_id=%s", entity_id
        )
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

def query_graph_tool(input_: dict) -> str:
    """Handler for the query_graph tool.

    Supported modes:
      ego      — RELATES_TO neighborhood of a single entity
      path     — shortest path between two entities (any edge type)
      mentions — information sources that MENTION a given entity

    No Cypher is exposed to the LLM. All traversal is bounded.

    Returns a JSON string with structured results plus a concise "summary" field
    for the LLM to include in its response.
    """
    mode = (input_.get("mode") or "").strip().lower()
    if mode not in ("ego", "path", "mentions"):
        return json.dumps(
            {"error": f"Unknown mode '{mode}'. Supported modes: ego, path, mentions"}
        )

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

    # Lumogis is single-user. user_id in tool input is optional; default is correct.
    user_id = (input_.get("user_id") or "default").strip() or "default"
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
        neighbor_names = [
            e["neighbor_name"] for e in edges[:5] if e.get("neighbor_name")
        ]
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
        return json.dumps(
            {"error": "from_entity and to_entity are required for mode=path"}
        )

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
                    f"{from_entity['name']} and {to_entity['name']} resolve "
                    f"to the same entity."
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
        summary = (
            f"{entity['name']} has no indexed mention sources in the graph yet."
        )
    else:
        type_counts: dict[str, int] = {}
        for s in sources:
            t = (s.get("evidence_type") or s.get("source_type") or "unknown").lower()
            type_counts[t] = type_counts.get(t, 0) + 1
        type_summary = ", ".join(
            f"{count} {label}(s)" for label, count in type_counts.items()
        )
        summary = (
            f"{entity['name']} is mentioned in {len(sources)} source(s): {type_summary}."
        )

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
    gated on MIN_MENTION_COUNT) then uses word-boundary regex matching against
    the lowercased query to prevent short entity names (e.g. "Ada") from
    matching inside longer words (e.g. "Canada").

    Complexity: O(100 × avg_aliases_per_entity) regex searches — typically < 1ms.

    Returns at most _CONTEXT_MAX_ENTITIES results in mention_count order.
    """
    ms = config.get_metadata_store()
    where_clause, where_params = visible_filter(UserContext(user_id=user_id))
    try:
        candidates = ms.fetch_all(
            "SELECT entity_id, name, entity_type, aliases, mention_count "
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
        if len(matched) >= _CONTEXT_MAX_ENTITIES:
            break
        if _term_in_query(row["name"], query_lower):
            matched.append(row)
            continue
        for alias in (row.get("aliases") or []):
            if _term_in_query(alias, query_lower):
                matched.append(row)
                break

    _log.info(
        "component=graph_context_building candidates=%d matched=%d",
        len(candidates),
        len(matched),
    )
    return matched


def on_context_building(*, query: str, context_fragments: list, **_kw) -> None:
    """Append lightweight graph context to context_fragments.

    Called synchronously from routes/chat.py _inject_context() via
    hooks.fire(Event.CONTEXT_BUILDING, ...).  Must be fast (<50ms target).

    For each entity detected in the query (max 3, gated on MIN_MENTION_COUNT):
      - Fetches its direct RELATES_TO neighbors (max 5 edges,
        co_occurrence_count >= COOCCURRENCE_THRESHOLD)
      - Appends a compact "[Graph] <name> relates to: <neighbor>, ..." line

    Gating:
      1. Graph backend must be available.
      2. Entity must appear in the query text (case-insensitive substring).
      3. Entity mention_count >= MIN_MENTION_COUNT.
      4. Entity must have at least one qualifying edge in FalkorDB.
      5. Max 3 entities injected per call.
      6. Max 5 edges per entity.

    Limitation: user_id is not in the CONTEXT_BUILDING hook payload (the hook
    fires before user_id is scoped to the query in chat.py). "default" is used,
    which is correct for all current single-user Lumogis deployments.
    """
    gs = config.get_graph_store()
    if gs is None:
        return

    t0 = time.monotonic()
    user_id = "default"

    entities = _detect_entities_in_query(query, user_id)
    if not entities:
        _log.debug(
            "component=graph_context_building injection=skipped reason=no_entity_match"
        )
        return

    lines: list[str] = []
    for entity in entities:
        result = ego_network(
            gs,
            entity["entity_id"],
            user_id,
            depth=1,
            limit=_CONTEXT_MAX_EDGES,
        )
        edges = result["edges"]
        if not edges:
            _log.debug(
                "component=graph_context_building entity_id=%s injection=skipped "
                "reason=no_qualifying_edges",
                entity["entity_id"],
            )
            continue

        # Include co_occurrence strength in parentheses when available so the
        # LLM can weight signal. Format: "Name (N)" — one extra token per edge.
        neighbor_parts = []
        for e in edges:
            n = e.get("neighbor_name")
            if not n:
                continue
            s = e.get("strength")
            neighbor_parts.append(f"{n} ({s})" if s is not None else n)
        lines.append(
            f"[Graph] {entity['name']} ({entity['entity_type']}) "
            f"relates to: {', '.join(neighbor_parts)}."
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
