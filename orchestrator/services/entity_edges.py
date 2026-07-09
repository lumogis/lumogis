# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""`entity_edges` writer — typed inter-entity relations (LUM-291).

Postgres is the system-of-record so relations survive the default
``GRAPH_BACKEND=none`` install. When a graph backend is configured the edge is
projected best-effort to FalkorDB; a projection failure never fails the
Postgres write (the graph is derived data).

``relation_type`` is validated against the UPPERCASE ``RELATION_TYPES``
allowlist before any DB or Cypher work — load-bearing, because the graph
projection interpolates the relation type into Cypher (which cannot bind a
relationship type as a parameter).
"""

from __future__ import annotations

import logging
import uuid

import config
from models.mcp_write import RELATION_TYPES
from services import banks

_log = logging.getLogger(__name__)


def store_edge(
    *,
    user_id: str,
    bank: str,
    src_entity_id: str,
    dst_entity_id: str,
    relation_type: str,
    evidence_id: str | None = None,
    ms=None,
) -> str:
    """UPSERT a typed directed edge. Returns the edge id (existing or new).

    Idempotent on ``(user_id, bank, src, dst, relation_type)`` via the UNIQUE
    index + ``ON CONFLICT DO NOTHING``; on conflict the existing row's id is
    returned.
    """
    rel = relation_type.strip().upper()
    if rel not in RELATION_TYPES:
        # Defence in depth — the input model also validates, but store_edge is a
        # public service entrypoint and this guards the Cypher projection.
        raise ValueError(f"relation_type {relation_type!r} not in {sorted(RELATION_TYPES)}")

    ms = ms or config.get_metadata_store()
    edge_id = uuid.uuid4().hex
    ms.execute(
        "INSERT INTO entity_edges "
        "(id, user_id, bank, src_entity_id, dst_entity_id, relation_type, evidence_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (user_id, bank, src_entity_id, dst_entity_id, relation_type) DO NOTHING",
        (edge_id, user_id, bank, src_entity_id, dst_entity_id, rel, evidence_id),
    )

    # Resolve the durable id (the row may pre-exist; our INSERT was a no-op).
    row = ms.fetch_one(
        # SCOPE-EXEMPT: entity_edges is scope-less (user_id-only); this is a
        # per-user read of the just-written edge, not a cross-user/god-mode read.
        "SELECT id FROM entity_edges WHERE user_id = %s AND bank = %s "
        "AND src_entity_id = %s AND dst_entity_id = %s AND relation_type = %s",
        (user_id, bank, src_entity_id, dst_entity_id, rel),
    )
    durable_id = row["id"] if row else edge_id

    # Stamp graph_projected_at only when the edge actually attached to BOTH
    # nodes (LUM-580). A first-mention entity's node is MERGE'd asynchronously
    # (fire_background), so the synchronous MATCH-only projection here often
    # no-ops; leaving the row unstamped lets reconcile_entity_edges replay it.
    if _project_edge(user_id, src_entity_id, dst_entity_id, rel, bank=bank):
        stamp_edge_projected(durable_id, ms=ms)
    return durable_id


def _project_edge(
    user_id: str,
    src_entity_id: str,
    dst_entity_id: str,
    rel: str,
    *,
    bank: str,
) -> bool:
    """Best-effort FalkorDB projection. No-op when graph is disabled.

    Returns ``True`` only when the typed edge actually attached to both nodes,
    ``False`` otherwise (graph disabled, a query failure, or — the common
    first-mention case — a node not yet MERGE'd so the MATCH-only projection
    no-ops). The boolean drives the ``graph_projected_at`` stamp in
    ``store_edge`` (LUM-580): a MATCH-only ``MERGE`` never raises when a node is
    absent, so "the query did not throw" is NOT a safe proxy for "attached" —
    we read ``count(r)`` back from the projection to know for certain.

    Goes through the ``GraphStore`` port (never a direct adapter import). A
    failure is logged and swallowed — Postgres remains the source of truth.
    ``rel`` is allowlist-validated (``^[A-Z_]+$``) before reaching the Cypher
    string, so the unavoidable relationship-type interpolation is safe.

    Nodes are matched on ``lumogis_id`` scoped by ``user_id`` — the contract the
    KG writer (``services/lumogis-graph/graph/writer.py``) creates entity nodes
    under. MATCH (not MERGE) on nodes: the projection overlays an edge onto
    writer-created nodes and must not create nodes here (LUM-566).
    """
    gs = config.get_graph_store(bank)
    if gs is None:
        return False
    try:
        rows = gs.query(
            "MATCH (a {lumogis_id: $src, user_id: $user_id}), "
            "(b {lumogis_id: $dst, user_id: $user_id}) "
            f"MERGE (a)-[r:{rel}]->(b) "
            "SET r.user_id = $user_id "
            "RETURN count(r) AS attached",
            {"src": src_entity_id, "dst": dst_entity_id, "user_id": user_id},
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "entity_edges: FalkorDB projection of %s-[%s]->%s failed (Postgres is SoR): %s",
            src_entity_id,
            rel,
            dst_entity_id,
            exc,
        )
        return False
    # Defensive parse: the projection result only drives a best-effort stamp, so
    # an unexpected shape must degrade to "not attached", never crash the write.
    if isinstance(rows, (list, tuple)) and rows and isinstance(rows[0], dict):
        return bool(rows[0].get("attached"))
    return False


def stamp_edge_projected(edge_id: str, *, ms=None) -> None:
    """Mark an ``entity_edges`` row as projected to FalkorDB (LUM-580).

    Written only after a confirmed edge attach (both nodes matched), so the
    reconcile replay pass re-projects only rows the live path missed. Best-effort
    — a stamp failure just means reconcile will re-attempt an already-attached
    (idempotent) MERGE next pass. Parameterised SQL; keys on the row ``id``.
    """
    ms = ms or config.get_metadata_store()
    try:
        ms.execute(
            "UPDATE entity_edges SET graph_projected_at = now() WHERE id = %s",
            (edge_id,),
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("entity_edges: graph_projected_at stamp failed for %s: %s", edge_id, exc)


def archive_edges_for_memory(memory_id: str, *, user_id: str, ms=None) -> None:
    """Soft-archive every edge whose evidence is ``memory_id`` (LUM-526).

    Used by ``forget`` / ``update_observation`` so a memory's typed relations
    drop out of recall along with the memory. Idempotent (``valid_until IS NULL``
    guard). Served by the ``idx_entity_edges_evidence`` index (migration 040).
    """
    ms = ms or config.get_metadata_store()
    ms.execute(
        "UPDATE entity_edges SET valid_until = now() "
        "WHERE evidence_id = %s AND user_id = %s AND valid_until IS NULL",
        (memory_id, user_id),
    )


def fetch_active_edges_for_memory(memory_id: str, *, user_id: str, ms=None) -> list[dict]:
    """Return active ``entity_edges`` rows for a memory (pre-archive snapshot).

    Used by ``forget`` / ``update_observation`` to purge FalkorDB projections on
    the correct bank graph before Postgres marks edges archived (LUM-544).
    """
    ms = ms or config.get_metadata_store()
    rows = ms.fetch_all(
        "SELECT bank, src_entity_id, dst_entity_id, relation_type "
        "FROM entity_edges "
        "WHERE evidence_id = %s AND user_id = %s AND valid_until IS NULL",
        (memory_id, user_id),
    )
    return list(rows or [])


def purge_graph_projections_for_edges(edges: list[dict], *, user_id: str) -> None:
    """Best-effort DELETE of projected FalkorDB rels for archived edges (LUM-544).

    Postgres remains the system of record; graph purge failures are logged and
    swallowed. ``relation_type`` is allowlist-validated before Cypher interpolation.

    Nodes are matched on ``lumogis_id`` scoped by ``user_id`` (writer contract,
    LUM-566). The edge predicate carries ``r.co_occurrence_count IS NULL``: the
    premium writer's co-occurrence tracker MERGEs an aggregate ``RELATES_TO`` edge
    onto the same node pair with a running ``co_occurrence_count``, so purging a
    forgotten memory's typed ``RELATES_TO`` relation must NOT delete that shared
    aggregate edge (which other still-active memories contributed to). The guard
    is a harmless no-op for non-``RELATES_TO`` types (they never carry the count).
    """
    if not edges:
        return
    by_bank: dict[str, list[dict]] = {}
    for edge in edges:
        bank = str(edge.get("bank") or "personal")
        by_bank.setdefault(bank, []).append(edge)
    for bank, bank_edges in by_bank.items():
        gs = config.get_graph_store(bank)
        if gs is None:
            continue
        for edge in bank_edges:
            rel = str(edge.get("relation_type") or "").strip().upper()
            if rel not in RELATION_TYPES:
                continue
            src = edge.get("src_entity_id")
            dst = edge.get("dst_entity_id")
            if not src or not dst:
                continue
            try:
                gs.query(
                    "MATCH (a {lumogis_id: $src, user_id: $user_id})-[r:"
                    f"{rel}"
                    "]->(b {lumogis_id: $dst, user_id: $user_id}) "
                    "WHERE r.user_id = $user_id AND r.co_occurrence_count IS NULL "
                    "DELETE r",
                    {"src": src, "dst": dst, "user_id": user_id},
                )
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "entity_edges: FalkorDB purge of %s-[%s]->%s on bank %s failed: %s",
                    src,
                    rel,
                    dst,
                    bank,
                    exc,
                )


def memories_for_entities(
    entity_ids: list[str],
    *,
    user_id: str,
    bank: str,
    as_of,
    hops: int = 1,
    ms=None,
) -> list[str]:
    """Return memory ids (``evidence_id``) connected to the seed entities (LUM-295).

    The recall graph leg: given seed ``entity_ids`` (from
    ``entities.entity_ids_for_query``), find the memories whose extracted typed
    relations touch any seed entity — i.e. ``entity_edges`` rows (currently
    valid at ``as_of``) whose ``src`` or ``dst`` is a seed, returning their
    ``evidence_id``. Bank- and user-scoped; temporal-filtered so archived edges
    drop out (makes LUM-526 observable on the graph leg too).

    ``hops`` is reserved for multi-hop expansion (a tracked follow-up); only
    ``hops == 1`` is implemented today — ``hops > 1`` raises
    ``NotImplementedError``. Empty ``entity_ids`` short-circuits to ``[]``.
    """
    if hops != 1:
        raise NotImplementedError("memories_for_entities: only hops=1 is implemented (LUM-295)")
    if not entity_ids:
        return []

    ms = ms or config.get_metadata_store()
    if banks.is_cross_bank(bank):
        rows = ms.fetch_all(
            # SCOPE-EXEMPT: entity_edges is scope-less (user_id-only); per-user read.
            "SELECT DISTINCT evidence_id FROM entity_edges "
            "WHERE user_id = %s "
            "AND (src_entity_id = ANY(%s) OR dst_entity_id = ANY(%s)) "
            "AND evidence_id IS NOT NULL "
            "AND (valid_until IS NULL OR valid_until >= %s)",
            (user_id, entity_ids, entity_ids, as_of),
        )
    else:
        rows = ms.fetch_all(
            # SCOPE-EXEMPT: entity_edges is scope-less (user_id-only); per-user read.
            "SELECT DISTINCT evidence_id FROM entity_edges "
            "WHERE user_id = %s AND bank = %s "
            "AND (src_entity_id = ANY(%s) OR dst_entity_id = ANY(%s)) "
            "AND evidence_id IS NOT NULL "
            "AND (valid_until IS NULL OR valid_until >= %s)",
            (user_id, bank, entity_ids, entity_ids, as_of),
        )
    return [str(r["evidence_id"]) for r in rows]
