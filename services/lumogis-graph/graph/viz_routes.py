# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""M4 visualization API endpoints.

Read-only, bounded, user-scoped graph queries for the Cytoscape.js viz page.

Endpoints
---------
GET /graph/ego    — ego network for a named entity
GET /graph/path   — shortest path between two named entities
GET /graph/search — entity name autocomplete for the viz search box
GET /graph/stats  — summary stats for the viz page header
GET /graph/viz    — serves the standalone HTML visualization page

Auth model
----------
AUTH_ENABLED=false (default): user_id = "default" for all requests.
AUTH_ENABLED=true:  user must be authenticated; user_id from JWT sub.
user_id is NEVER taken from query parameters.

Limits
------
GRAPH_VIZ_MAX_NODES (env, default 150): hard node cap per response.
GRAPH_VIZ_MAX_EDGES (env, default 300): hard edge cap per response.
When a result would exceed caps, result is truncated and truncated=true is set.

Graceful degradation
--------------------
All endpoints return a structured JSON response when FalkorDB is unavailable.
No endpoint raises 5xx for graph unavailability — only for auth/input errors.
"""

import logging
import os
import time
from pathlib import Path

import config
from auth import UserContext
from auth import get_user
from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import FileResponse

from graph.query import resolve_entity_by_name
from graph.schema import COOCCURRENCE_THRESHOLD
from visibility import visible_cypher_fragment, visible_filter

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/graph", tags=["graph"])

_STATIC_DIR = Path(__file__).parent.parent.parent / "static"
_VIZ_HTML = _STATIC_DIR / "graph_viz.html"


def _max_nodes() -> int:
    return config.get_graph_viz_max_nodes()


def _max_edges() -> int:
    return config.get_graph_viz_max_edges()


def _require_auth(request: Request) -> str:
    """Enforce authentication and return user_id.

    When AUTH_ENABLED=false → always returns "default".
    When AUTH_ENABLED=true  → user must be authenticated; 401 if not.
    user_id is NEVER sourced from query parameters.
    """
    user = get_user(request)
    if os.environ.get("AUTH_ENABLED", "false").lower() == "true" and not user.is_authenticated:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user.user_id


def _graph_unavailable_response() -> dict:
    return {
        "available": False,
        "message": (
            "FalkorDB graph is not available. "
            "Set GRAPH_BACKEND=falkordb and start FalkorDB to enable graph features."
        ),
    }


# ---------------------------------------------------------------------------
# Helpers — build viz DTOs from FalkorDB rows
# ---------------------------------------------------------------------------

def _entity_row_to_node(row: dict, center: bool = False) -> dict:
    """Convert a raw query row to a viz node dict."""
    label_raw = (row.get("entity_type") or row.get("node_type") or "CONCEPT").upper()
    return {
        "id": str(row.get("entity_id") or row.get("lumogis_id") or ""),
        "label": (row.get("name") or "")[:40],
        "type": label_raw,
        "mention_count": int(row.get("mention_count") or 0),
        "center": center,
    }


def _safe_query(gs, cypher: str, params: dict) -> list[dict]:
    """Execute a graph query; return [] on any error."""
    try:
        return gs.query(cypher, params)
    except Exception:
        _log.exception("viz_routes: graph query failed")
        return []


# ---------------------------------------------------------------------------
# GET /graph/ego
# ---------------------------------------------------------------------------

@router.get("/ego")
def get_ego(
    request: Request,
    entity: str,
    depth: int = 1,
    limit: int = 50,
    min_strength: int = 0,
):
    """Return ego network for a named entity.

    Parameters
    ----------
    entity       : entity name or alias (required)
    depth        : traversal depth (capped at 1 for now, consistent with M3)
    limit        : max nodes in result (default 50, absolute max=GRAPH_VIZ_MAX_NODES)
    min_strength : filter RELATES_TO edges below this co-occurrence count
    """
    user_id = _require_auth(request)

    if not entity or not entity.strip():
        raise HTTPException(status_code=400, detail="entity parameter is required")

    entity_name = entity.strip()
    max_nodes = _max_nodes()
    max_edges = _max_edges()
    effective_limit = min(max(int(limit), 1), max_nodes)
    effective_min_strength = max(int(min_strength), 0)
    # Enforce threshold: never go below the schema threshold
    strength_threshold = max(effective_min_strength, COOCCURRENCE_THRESHOLD)

    gs = config.get_graph_store()
    if gs is None:
        return _graph_unavailable_response()

    center_entity = resolve_entity_by_name(entity_name, user_id)
    if center_entity is None:
        return {
            "available": True,
            "found": False,
            "entity": entity_name,
            "message": "Entity not found in the knowledge graph.",
            "nodes": [],
            "edges": [],
            "truncated": False,
            "node_count": 0,
            "edge_count": 0,
        }

    center_id = center_entity["entity_id"]
    t0 = time.monotonic()

    # Fetch direct RELATES_TO neighbors with optional strength filter.
    user = UserContext(user_id=user_id)
    center_vis, vis_params = visible_cypher_fragment(user, alias="center")
    neighbor_vis, _ = visible_cypher_fragment(user, alias="neighbor")
    cypher = (
        "MATCH (center)-[r:RELATES_TO]-(neighbor) "
        "WHERE center.lumogis_id = $eid "
        f"  AND {center_vis} "
        f"  AND {neighbor_vis} "
        f"  AND r.co_occurrence_count >= {strength_threshold} "
        "RETURN neighbor.lumogis_id AS neighbor_id, "
        "       neighbor.name AS neighbor_name, "
        "       neighbor.entity_type AS neighbor_type, "
        "       r.co_occurrence_count AS strength "
        "ORDER BY strength DESC "
        f"LIMIT {effective_limit}"
    )
    rows = _safe_query(gs, cypher, {"eid": center_id, **vis_params})

    truncated = False
    if len(rows) >= effective_limit:
        truncated = True

    # Build node list: center first, then neighbors
    nodes: list[dict] = [
        {
            "id": center_entity["entity_id"],
            "label": (center_entity.get("name") or "")[:40],
            "type": (center_entity.get("entity_type") or "CONCEPT").upper(),
            "mention_count": int(center_entity.get("mention_count") or 0),
            "center": True,
        }
    ]
    seen_ids = {center_entity["entity_id"]}

    edges: list[dict] = []
    for r in rows:
        nid = str(r.get("neighbor_id") or "")
        if not nid or nid == center_id:
            continue
        if len(nodes) >= max_nodes:
            truncated = True
            break
        if nid not in seen_ids:
            seen_ids.add(nid)
            nodes.append({
                "id": nid,
                "label": (r.get("neighbor_name") or "")[:40],
                "type": (r.get("neighbor_type") or "CONCEPT").upper(),
                "mention_count": 0,
                "center": False,
            })
        if len(edges) < max_edges:
            edges.append({
                "source": center_id,
                "target": nid,
                "type": "RELATES_TO",
                "strength": int(r.get("strength") or 0),
            })
        else:
            truncated = True

    duration_ms = int((time.monotonic() - t0) * 1000)
    _log.info(
        "component=graph_viz mode=ego nodes=%d edges=%d truncated=%s duration_ms=%d",
        len(nodes), len(edges), truncated, duration_ms,
    )

    return {
        "available": True,
        "found": True,
        "entity_id": center_entity["entity_id"],
        "entity_name": center_entity.get("name") or entity_name,
        "entity_type": (center_entity.get("entity_type") or "CONCEPT").upper(),
        "nodes": nodes,
        "edges": edges,
        "truncated": truncated,
        "node_count": len(nodes),
        "edge_count": len(edges),
    }


# ---------------------------------------------------------------------------
# GET /graph/path
# ---------------------------------------------------------------------------

@router.get("/path")
def get_path(
    request: Request,
    from_entity: str,
    to_entity: str,
    max_depth: int = 4,
):
    """Return shortest path between two named entities.

    Parameters
    ----------
    from_entity : starting entity name (required)
    to_entity   : target entity name (required)
    max_depth   : maximum path length to search (default 4, max 4)
    """
    user_id = _require_auth(request)

    if not from_entity or not from_entity.strip():
        raise HTTPException(status_code=400, detail="from_entity parameter is required")
    if not to_entity or not to_entity.strip():
        raise HTTPException(status_code=400, detail="to_entity parameter is required")

    from_name = from_entity.strip()
    to_name = to_entity.strip()
    effective_depth = min(max(int(max_depth), 1), 4)

    gs = config.get_graph_store()
    if gs is None:
        return _graph_unavailable_response()

    from_ent = resolve_entity_by_name(from_name, user_id)
    if from_ent is None:
        return {
            "available": True,
            "found": False,
            "from_entity": from_name,
            "to_entity": to_name,
            "message": f"'{from_name}' not found in the knowledge graph.",
            "path_found": False,
            "nodes": [],
            "edges": [],
        }

    to_ent = resolve_entity_by_name(to_name, user_id)
    if to_ent is None:
        return {
            "available": True,
            "found": False,
            "from_entity": from_name,
            "to_entity": to_name,
            "message": f"'{to_name}' not found in the knowledge graph.",
            "path_found": False,
            "nodes": [],
            "edges": [],
        }

    from_id = from_ent["entity_id"]
    to_id = to_ent["entity_id"]
    t0 = time.monotonic()

    # Same entity: trivial path
    if from_id == to_id:
        node = {
            "id": from_id,
            "label": (from_ent.get("name") or "")[:40],
            "type": (from_ent.get("entity_type") or "CONCEPT").upper(),
            "mention_count": int(from_ent.get("mention_count") or 0),
            "center": True,
        }
        return {
            "available": True,
            "found": True,
            "path_found": True,
            "path_length": 0,
            "from_entity": from_ent.get("name") or from_name,
            "to_entity": to_ent.get("name") or to_name,
            "nodes": [node],
            "edges": [],
            "truncated": False,
            "node_count": 1,
            "edge_count": 0,
        }

    # FalkorDB does not support shortestPath() in MATCH and rejects the undirected
    # form in WITH/RETURN. Use the algo.SPpaths procedure with relDirection 'both'
    # so we traverse RELATES_TO / MENTIONS / DISCUSSED_IN regardless of orientation.
    # Endpoints visibility-gated via visible_cypher_fragment; interior path nodes
    # are not scope-checked (caller already resolved both endpoints through
    # resolve_entity_by_name which IS visibility-gated).
    user = UserContext(user_id=user_id)
    a_vis, vis_params = visible_cypher_fragment(user, alias="a")
    b_vis, _ = visible_cypher_fragment(user, alias="b")
    cypher = (
        "MATCH (a), (b) "
        f"WHERE a.lumogis_id = $from_id AND {a_vis} "
        f"  AND b.lumogis_id = $to_id   AND {b_vis} "
        "WITH a, b "
        f"CALL algo.SPpaths({{sourceNode: a, targetNode: b, "
        f"maxLen: {effective_depth}, pathCount: 1, relDirection: 'both'}}) "
        "YIELD path "
        "RETURN [n IN nodes(path) | n.lumogis_id] AS node_ids, "
        "       [n IN nodes(path) | n.name] AS node_names, "
        "       [n IN nodes(path) | n.entity_type] AS node_types, "
        "       [r IN relationships(path) | type(r)] AS rel_types, "
        "       length(path) AS path_length"
    )
    rows = _safe_query(gs, cypher, {"from_id": from_id, "to_id": to_id, **vis_params})

    duration_ms = int((time.monotonic() - t0) * 1000)

    if not rows:
        _log.info(
            "component=graph_viz mode=path path_found=false duration_ms=%d",
            duration_ms,
        )
        return {
            "available": True,
            "found": True,
            "path_found": False,
            "from_entity": from_ent.get("name") or from_name,
            "to_entity": to_ent.get("name") or to_name,
            "message": (
                f"No path found between '{from_ent.get('name') or from_name}' "
                f"and '{to_ent.get('name') or to_name}' within {effective_depth} hops."
            ),
            "nodes": [],
            "edges": [],
            "node_count": 0,
            "edge_count": 0,
            "truncated": False,
        }

    row = rows[0]
    node_ids: list = row.get("node_ids") or []
    node_names: list = row.get("node_names") or []
    node_types: list = row.get("node_types") or []
    rel_types: list = row.get("rel_types") or []
    path_length = int(row.get("path_length") or 0)

    nodes: list[dict] = []
    for i, nid in enumerate(node_ids):
        if not nid:
            continue
        nodes.append({
            "id": str(nid),
            "label": (str(node_names[i]) if i < len(node_names) else "")[:40],
            "type": (str(node_types[i]).upper() if i < len(node_types) else "CONCEPT"),
            "mention_count": 0,
            "center": (str(nid) == from_id or str(nid) == to_id),
        })

    edges: list[dict] = []
    for i in range(len(node_ids) - 1):
        src = str(node_ids[i]) if i < len(node_ids) else ""
        tgt = str(node_ids[i + 1]) if (i + 1) < len(node_ids) else ""
        rel = str(rel_types[i]) if i < len(rel_types) else "RELATES_TO"
        if src and tgt:
            edges.append({"source": src, "target": tgt, "type": rel, "strength": 1})

    _log.info(
        "component=graph_viz mode=path path_found=true length=%d duration_ms=%d",
        path_length, duration_ms,
    )
    return {
        "available": True,
        "found": True,
        "path_found": True,
        "path_length": path_length,
        "from_entity": from_ent.get("name") or from_name,
        "to_entity": to_ent.get("name") or to_name,
        "nodes": nodes,
        "edges": edges,
        "truncated": False,
        "node_count": len(nodes),
        "edge_count": len(edges),
    }


# ---------------------------------------------------------------------------
# GET /graph/search
# ---------------------------------------------------------------------------

@router.get("/search")
def search_entities(
    request: Request,
    q: str,
    limit: int = 10,
):
    """Search for entities by partial name — for the viz page autocomplete.

    Parameters
    ----------
    q     : partial name query (minimum 2 characters)
    limit : max results (default 10, max 20)
    """
    user_id = _require_auth(request)

    q = (q or "").strip()
    if len(q) < 2:
        return {"results": [], "message": "Query must be at least 2 characters."}

    effective_limit = min(max(int(limit), 1), 20)

    ms = config.get_metadata_store()
    where_clause, where_params = visible_filter(UserContext(user_id=user_id))
    try:
        rows = ms.fetch_all(
            "SELECT entity_id, name, entity_type, mention_count, scope "
            "FROM entities "
            f"WHERE {where_clause} AND lower(name) LIKE lower(%s) "
            "ORDER BY mention_count DESC "
            "LIMIT %s",
            (*where_params, f"%{q}%", effective_limit),
        )
    except Exception:
        _log.exception("graph_viz: entity search failed")
        return {"results": []}

    results = [
        {
            "entity_id": str(r.get("entity_id") or ""),
            "name": r.get("name") or "",
            "type": (r.get("entity_type") or "CONCEPT").upper(),
            "mention_count": int(r.get("mention_count") or 0),
            "scope": r.get("scope", "personal"),
        }
        for r in rows
    ]

    return {"results": results}


# ---------------------------------------------------------------------------
# GET /graph/stats
# ---------------------------------------------------------------------------

@router.get("/stats")
def get_stats(request: Request):
    """Return summary statistics for the viz page header.

    Visibility: stats are scoped to what the caller can see — i.e. the
    household-visible union via :func:`visibility.visible_cypher_fragment`
    on the FalkorDB side and :func:`visibility.visible_filter` on the
    Postgres side.

    Always returns a response — never 5xx for graph unavailability.
    """
    user_id = _require_auth(request)
    user = UserContext(user_id=user_id)

    gs = config.get_graph_store()
    if gs is None:
        return {
            "available": False,
            "message": (
                "FalkorDB graph is not available. "
                "Set GRAPH_BACKEND=falkordb to enable graph features."
            ),
            "node_count": 0,
            "edge_count": 0,
            "top_entities": [],
        }

    t0 = time.monotonic()

    # Node count — visibility-scoped to the caller (household union by default).
    node_count = 0
    node_vis, vis_params = visible_cypher_fragment(user, alias="n")
    try:
        rows = gs.query(
            f"MATCH (n) WHERE {node_vis} RETURN count(n) AS cnt", vis_params
        )
        if rows:
            node_count = int(rows[0].get("cnt") or 0)
    except Exception:
        _log.warning("graph_viz: stats node_count query failed")

    # Edge count — count edges between visible nodes only.
    edge_count = 0
    src_vis, _ = visible_cypher_fragment(user, alias="src")
    tgt_vis, _ = visible_cypher_fragment(user, alias="tgt")
    try:
        rows = gs.query(
            f"MATCH (src)-[r]->(tgt) WHERE {src_vis} AND {tgt_vis} "
            "RETURN count(r) AS cnt",
            vis_params,
        )
        if rows:
            edge_count = int(rows[0].get("cnt") or 0)
    except Exception:
        _log.warning("graph_viz: stats edge_count query failed")

    # Top entities by mention_count from Postgres (more reliable than FalkorDB for this).
    top_entities: list[dict] = []
    where_clause, where_params = visible_filter(user)
    try:
        ms = config.get_metadata_store()
        pg_rows = ms.fetch_all(
            "SELECT name, entity_type, mention_count, scope "
            "FROM entities "
            f"WHERE {where_clause} "
            "ORDER BY mention_count DESC "
            "LIMIT 5",
            where_params,
        )
        top_entities = [
            {
                "name": r.get("name") or "",
                "type": (r.get("entity_type") or "CONCEPT").upper(),
                "mention_count": int(r.get("mention_count") or 0),
                "scope": r.get("scope", "personal"),
            }
            for r in pg_rows
        ]
    except Exception:
        _log.warning("graph_viz: stats top_entities query failed")

    duration_ms = int((time.monotonic() - t0) * 1000)
    _log.info(
        "component=graph_viz mode=stats nodes=%d edges=%d duration_ms=%d",
        node_count, edge_count, duration_ms,
    )

    return {
        "available": True,
        "node_count": node_count,
        "edge_count": edge_count,
        "top_entities": top_entities,
        "cooccurrence_threshold": config.get_cooccurrence_threshold(),
    }


# ---------------------------------------------------------------------------
# GET /graph/viz  — serve the standalone visualization page
# ---------------------------------------------------------------------------

@router.get("/viz")
def graph_viz(request: Request):
    """Serve the Cytoscape.js knowledge graph visualization page."""
    _require_auth(request)
    if not _VIZ_HTML.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                "Visualization page not found. "
                "Check that orchestrator/static/graph_viz.html exists."
            ),
        )
    return FileResponse(_VIZ_HTML, media_type="text/html")
