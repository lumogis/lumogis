# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""KG-side projection helpers for the personal/shared/system scope model.

The orchestrator owns the Postgres + Qdrant projection mirror (see
``orchestrator/services/projection.py``). This module owns the
**FalkorDB shared-graph projection**: when a user publishes a
personal entity to the household, the KG service must materialise a
companion ``(scope='shared')`` node and sweep every incident edge
that connects two now-shared endpoints into the shared graph.

Per plan §7 step 4a, the orchestrator publishes Postgres+Qdrant
first (with deterministic uuid5 ids); the KG reconciler then catches
up on the next pass via :func:`project_entity_into_graph`. This split
keeps the publish path's HTTP round-trip free of an extra service hop
and lets the KG service stay the single writer of FalkorDB.

Idempotency: the FalkorDB MERGE pattern keys nodes on
``(lumogis_id, scope, user_id)`` so repeated calls converge on a
single shared node. Projected ``RELATES_TO`` edges MERGE on
``(lower_node, RELATES_TO, scope, higher_node)`` (lexicographic
``lumogis_id`` order, mirroring ``graph/writer.py``); ``evidence_id`` is
applied only via ``SET``, not in the MERGE pattern map — see
``adapters/falkordb_store.py`` for node MERGE conventions.

Drift discipline: the orchestrator-side helpers are imported by the
publish routes; this module is called from the KG-side reconciler.
The two sides communicate only via the shared Postgres
``published_from`` column — there is no direct service-to-service
RPC.

See also: ``.cursor/plans/personal_shared_system_memory_scopes.plan.md``
``.cursor/adrs/personal_shared_system_memory_scopes.md``
"""

from __future__ import annotations

import logging
from typing import Any

import config

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Entity projection (called from reconciler / publish hook)
# ---------------------------------------------------------------------------


def project_entity_into_graph(
    *,
    src_entity_id: str,
    proj_entity_id: str,
    target_scope: str,
    actor_user_id: str,
    name: str,
    entity_type: str,
) -> bool:
    """MERGE the shared/system entity node and sweep incident edges.

    The node contract (``scope`` + ``published_from``) is the one
    :func:`graph.schema.node_scope` enforces for the Session/Note/AudioMemo
    projections (LUM-961); this function keeps its own ``target_scope`` guard.

    Called after the orchestrator commits the Postgres+Qdrant
    projection (plan §7 step 4a). Returns ``True`` when the FalkorDB
    write succeeds; ``False`` on backend failure (so the caller can
    retry on the next reconcile pass without flipping
    ``graph_projected_at``).

    The edge sweep walks every ``RELATES_TO`` edge incident to the
    source node and, for each one whose **other** endpoint also has
    a projection at ``target_scope``, MERGEs an equivalent edge
    between the two projection nodes. Edges whose other endpoint has
    no projection are intentionally left in the personal-only graph
    (plan §7.2 rule "shared edges require two shared endpoints").
    """
    if target_scope not in ("shared", "system"):
        raise ValueError(f"target_scope must be 'shared' or 'system'; got {target_scope!r}")

    gs = config.get_graph_store()
    if gs is None:
        _log.warning(
            "project_entity_into_graph: graph store unavailable src=%s proj=%s scope=%s",
            src_entity_id,
            proj_entity_id,
            target_scope,
        )
        return False

    try:
        gs.create_node(
            labels=["Entity"],
            properties={
                "lumogis_id": proj_entity_id,
                "user_id": actor_user_id,
                "scope": target_scope,
                "name": name,
                "entity_type": entity_type,
                "published_from": src_entity_id,
            },
        )
    except Exception as exc:
        _log.warning(
            "project_entity_into_graph: node MERGE failed src=%s proj=%s — %s",
            src_entity_id,
            proj_entity_id,
            exc,
        )
        return False

    try:
        swept = _sweep_incident_edges(
            gs,
            src_entity_id=src_entity_id,
            proj_entity_id=proj_entity_id,
            target_scope=target_scope,
            actor_user_id=actor_user_id,
        )
    except Exception as exc:
        _log.warning(
            "project_entity_into_graph: edge sweep failed src=%s proj=%s — %s",
            src_entity_id,
            proj_entity_id,
            exc,
        )
        return False

    _log.info(
        "project_entity_into_graph: src=%s proj=%s scope=%s actor=%s swept_edges=%d",
        src_entity_id,
        proj_entity_id,
        target_scope,
        actor_user_id,
        swept,
    )
    return True


def _sweep_incident_edges(
    gs: Any,
    *,
    src_entity_id: str,
    proj_entity_id: str,
    target_scope: str,
    actor_user_id: str,
) -> int:
    """Backward-sweep edges from the personal entity into the shared graph.

    For every personal-graph edge ``(src)-[r]-(other)`` where ``other``
    has its own projection at ``target_scope``, MERGE a parallel **directed**
    ``RELATES_TO`` edge between the projection nodes (lower ``lumogis_id``
    → higher) carrying co-occurrence and evidence fields so de-dup stays
    stable across re-publishes. FalkorDB rejects **undirected** relationship
    MERGE patterns; incident ``MATCH`` stays undirected.

    Personal-node model (LUM-586 fix, LUM-961): ``graph/writer.py::project_entity``
    writes personal entity nodes with a **concrete type label** (``:Person``,
    ``:Organisation`` …) keyed on ``(lumogis_id, user_id)`` and ``scope =
    'personal'``. The shared/system projection nodes carry ``:Entity`` +
    ``scope``. The historical ``(:Entity {scope:'personal'})`` MATCH matched
    nothing because personal nodes have a type label, not ``:Entity``. The
    personal matches below are label-agnostic and select personal nodes by
    ``scope = 'personal'``; the projection matches keep the ``:Entity`` + ``scope``
    shape from ``project_entity_into_graph``. Nodes without ``scope`` are
    pre-LUM-961 leftovers that the sweep deliberately ignores (no fallback; Thomas,
    2026-11-11): a share that sweeps nothing shows as ``swept_edges=0`` in the log.

    Returns the number of projection edges merged.

    Implemented as a single Cypher round-trip with ``WITH`` + ``CASE`` for
    endpoint ordering. If a FalkorDB build rejects ``CASE``-bound node
    variables, fall back to split queries (see plan LUM-208).
    """
    if target_scope not in ("shared", "system"):
        raise ValueError(f"target_scope must be 'shared' or 'system'; got {target_scope!r}")

    cypher = (
        "MATCH (src {lumogis_id: $src_id}) "
        "WHERE src.user_id = $actor AND src.scope = 'personal' "
        "MATCH (src)-[r:RELATES_TO]-(other) "
        "WHERE other.user_id = $actor AND other.scope = 'personal' "
        "MATCH (proj:Entity {lumogis_id: $proj_id, scope: $target_scope}) "
        "WHERE proj.user_id = $actor "
        "MATCH (other_proj:Entity "
        "{published_from: other.lumogis_id, scope: $target_scope}) "
        "WHERE other_proj.user_id = $actor "
        "WITH proj, other_proj, r, "
        "CASE WHEN proj.lumogis_id < other_proj.lumogis_id "
        "THEN proj ELSE other_proj END AS lower_node, "
        "CASE WHEN proj.lumogis_id < other_proj.lumogis_id "
        "THEN other_proj ELSE proj END AS higher_node "
        "MERGE (lower_node)-[r2:RELATES_TO { scope: $target_scope }]->(higher_node) "
        "SET r2.co_occurrence_count = coalesce(r.co_occurrence_count, 0), "
        "r2.user_id = $actor, "
        "r2.evidence_id = r.evidence_id, "
        "r2.published_from_evidence = r.evidence_id "
        "RETURN count(r2) AS swept"
    )
    rows = gs.query(
        cypher,
        {
            "src_id": src_entity_id,
            "proj_id": proj_entity_id,
            "target_scope": target_scope,
            "actor": actor_user_id,
        },
    )
    if not rows:
        return 0
    first = rows[0]
    swept = first.get("swept", 0) if isinstance(first, dict) else first[0]
    return int(swept or 0)


# ---------------------------------------------------------------------------
# Entity unprojection
# ---------------------------------------------------------------------------


def unproject_entity_from_graph(
    *,
    src_entity_id: str,
    target_scope: str = "shared",
) -> bool:
    """Detach + delete the shared/system projection node and its edges.

    Called from the orchestrator unpublish path after the Postgres
    projection row is deleted (plan §7 unpublish step 6). Returns
    ``True`` when the FalkorDB write succeeds; ``False`` on backend
    failure — the orchestrator surface logs the failure and continues
    so an unpublish never blocks on a stale graph node.

    Uses ``DETACH DELETE`` so all incident shared edges come down
    with the node in a single round trip.
    """
    gs = config.get_graph_store()
    if gs is None:
        _log.warning(
            "unproject_entity_from_graph: graph store unavailable src=%s",
            src_entity_id,
        )
        return False

    try:
        gs.query(
            "MATCH (n:Entity {published_from: $src_id, scope: $scope}) DETACH DELETE n",
            {"src_id": src_entity_id, "scope": target_scope},
        )
    except Exception as exc:
        _log.warning(
            "unproject_entity_from_graph: DETACH DELETE failed src=%s — %s",
            src_entity_id,
            exc,
        )
        return False

    _log.info(
        "unproject_entity_from_graph: src=%s scope=%s",
        src_entity_id,
        target_scope,
    )
    return True


# ---------------------------------------------------------------------------
# Merge-driven graph remap (called from KG-side merge reconciler)
# ---------------------------------------------------------------------------


def remap_published_from_in_graph(
    *,
    loser_id: str,
    winner_id: str,
) -> bool:
    """Repoint shared/system projection nodes whose ``published_from``
    pointed at ``loser_id`` to ``winner_id`` after an entity merge.

    Mirror of ``orchestrator/services/projection.remap_published_from``
    on the FalkorDB side (plan §2.11 rule 31). The Postgres remap is
    authoritative; this call keeps the shared graph in sync so a
    follow-up ego-network query against ``winner_id`` surfaces edges
    that previously pointed at the merged-away node.

    Returns ``True`` on success; ``False`` if the graph backend is
    unavailable (the merge itself still succeeded — the next
    reconcile pass will retry).
    """
    gs = config.get_graph_store()
    if gs is None:
        _log.warning(
            "remap_published_from_in_graph: graph store unavailable loser=%s winner=%s",
            loser_id,
            winner_id,
        )
        return False

    try:
        gs.query(
            "MATCH (n:Entity) "
            "WHERE n.published_from = $loser_id AND n.scope IN ['shared', 'system'] "
            "SET n.published_from = $winner_id",
            {"loser_id": loser_id, "winner_id": winner_id},
        )
    except Exception as exc:
        _log.warning(
            "remap_published_from_in_graph: failed loser=%s winner=%s — %s",
            loser_id,
            winner_id,
            exc,
        )
        return False

    _log.info(
        "remap_published_from_in_graph: loser=%s winner=%s",
        loser_id,
        winner_id,
    )
    return True


__all__ = [
    "project_entity_into_graph",
    "unproject_entity_from_graph",
    "remap_published_from_in_graph",
]
