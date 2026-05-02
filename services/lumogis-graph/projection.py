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
single shared node. The edge sweep keys on
``(from_id, to_id, rel_type, evidence_id, scope)`` for the same
reason — see ``adapters/falkordb_store.py`` for the underlying MERGE
contract.

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
from typing import Any, Optional

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
        raise ValueError(
            f"target_scope must be 'shared' or 'system'; got {target_scope!r}"
        )

    gs = config.get_graph_store()
    if gs is None:
        _log.warning(
            "project_entity_into_graph: graph store unavailable "
            "src=%s proj=%s scope=%s",
            src_entity_id, proj_entity_id, target_scope,
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
            src_entity_id, proj_entity_id, exc,
        )
        return False

    try:
        _sweep_incident_edges(
            gs,
            src_entity_id=src_entity_id,
            proj_entity_id=proj_entity_id,
            target_scope=target_scope,
            actor_user_id=actor_user_id,
        )
    except Exception as exc:
        _log.warning(
            "project_entity_into_graph: edge sweep failed src=%s proj=%s — %s",
            src_entity_id, proj_entity_id, exc,
        )
        return False

    _log.info(
        "project_entity_into_graph: src=%s proj=%s scope=%s actor=%s",
        src_entity_id, proj_entity_id, target_scope, actor_user_id,
    )
    return True


def _sweep_incident_edges(
    gs: Any,
    *,
    src_entity_id: str,
    proj_entity_id: str,
    target_scope: str,
    actor_user_id: str,
) -> None:
    """Backward-sweep edges from the personal entity into the shared graph.

    For every personal-graph edge ``(src)-[r]-(other)`` where ``other``
    has its own projection at ``target_scope``, MERGE a parallel edge
    ``(proj)-[r']-(other_proj)`` carrying the same ``rel_type`` and
    ``evidence_id`` so de-dup remains stable across re-publishes.

    Implemented as a single Cypher query so we avoid a per-edge round
    trip; FalkorDB's planner handles the MERGE-over-MATCH pattern.
    """
    cypher = (
        "MATCH (src:Entity {lumogis_id: $src_id, scope: 'personal'}) "
        "MATCH (src)-[r:RELATES_TO]-(other:Entity {scope: 'personal'}) "
        "MATCH (proj:Entity {lumogis_id: $proj_id, scope: $target_scope}) "
        "MATCH (other_proj:Entity "
        "       {published_from: other.lumogis_id, scope: $target_scope}) "
        "MERGE (proj)-[r2:RELATES_TO {evidence_id: r.evidence_id, "
        "                              scope: $target_scope}]-(other_proj) "
        "SET r2.co_occurrence_count = coalesce(r.co_occurrence_count, 0), "
        "    r2.user_id = $actor, "
        "    r2.published_from_evidence = r.evidence_id"
    )
    gs.query(
        cypher,
        {
            "src_id": src_entity_id,
            "proj_id": proj_entity_id,
            "target_scope": target_scope,
            "actor": actor_user_id,
        },
    )


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
            "MATCH (n:Entity {published_from: $src_id, scope: $scope}) "
            "DETACH DELETE n",
            {"src_id": src_entity_id, "scope": target_scope},
        )
    except Exception as exc:
        _log.warning(
            "unproject_entity_from_graph: DETACH DELETE failed src=%s — %s",
            src_entity_id, exc,
        )
        return False

    _log.info(
        "unproject_entity_from_graph: src=%s scope=%s",
        src_entity_id, target_scope,
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
            "remap_published_from_in_graph: graph store unavailable "
            "loser=%s winner=%s",
            loser_id, winner_id,
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
            loser_id, winner_id, exc,
        )
        return False

    _log.info(
        "remap_published_from_in_graph: loser=%s winner=%s",
        loser_id, winner_id,
    )
    return True


__all__ = [
    "project_entity_into_graph",
    "unproject_entity_from_graph",
    "remap_published_from_in_graph",
]
