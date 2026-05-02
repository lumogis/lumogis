# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Graph writer: hook callbacks that project Lumogis events into FalkorDB.

Each public on_* function is registered as a hook handler in plugins/graph/__init__.py.
All functions are designed to be called from hooks.fire_background() — they
run in the hooks ThreadPoolExecutor and must be thread-safe.

Projection helpers
------------------
Each projection unit is exposed as a public _project_* function that accepts
explicit arguments drawn from Postgres rows.  The hook handlers are thin
wrappers that parse hook payloads and delegate to these helpers.

The reconciliation module (reconcile.py) imports and calls the same helpers
directly, ensuring that scheduled reconciliation and live hook handling use
identical graph-write logic with no drift.

Log policy
----------
NEVER log raw user text (note content, session summaries, transcript text).
Log only IDs, types, counts, and truncated previews (max 80 chars).
This prevents sensitive user data from appearing in application logs.

Projection units
----------------
On success: write to FalkorDB, then stamp graph_projected_at in Postgres.
On any failure: log the error and return. Do NOT stamp graph_projected_at.
The reconciliation job will retry the next cycle.

Idempotency
-----------
All graph writes use MERGE with deterministic match keys. Re-processing
the same event is always safe — no duplicates can result.
"""

import logging
import os
from datetime import datetime
from datetime import timezone

import config
from graph.schema import EdgeType
from graph.schema import MAX_TEXT_LENGTH
from graph.schema import NodeLabel

_log = logging.getLogger(__name__)

_PREVIEW_LEN = 80


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(text: str | None, max_len: int = MAX_TEXT_LENGTH) -> str:
    if not text:
        return ""
    return text[:max_len]


def _preview(text: str | None) -> str:
    if not text:
        return ""
    s = text[:_PREVIEW_LEN]
    return s + "…" if len(text) > _PREVIEW_LEN else s


# ---------------------------------------------------------------------------
# Shared projection helpers (called by hook handlers AND reconciliation)
# ---------------------------------------------------------------------------

def project_document(
    gs,
    *,
    file_path: str,
    file_type: str,
    user_id: str,
    ms=None,
) -> None:
    """Project a document row into FalkorDB and stamp graph_projected_at.

    Creates or merges the Document node.  Stamps file_index.graph_projected_at
    only after a successful write.  Raises on failure (caller decides to stamp
    or not).
    """
    ext = file_type or os.path.splitext(file_path)[1].lstrip(".").lower() or "unknown"
    node_id = gs.create_node(
        labels=[NodeLabel.DOCUMENT],
        properties={
            "lumogis_id": file_path,
            "file_path": file_path,
            "file_type": ext,
            "user_id": user_id,
            "ingested_at": _now_iso(),
        },
    )
    _log.debug(
        "Graph: Document node merged file_path=%r node_id=%s",
        _preview(file_path),
        node_id,
    )
    _stamp_graph_projected_at("file_index", "file_path", file_path, user_id, ms=ms)


def project_entity(
    gs,
    *,
    entity_id: str,
    entity_type: str,
    name: str,
    evidence_id: str,
    evidence_type: str,
    user_id: str,
    ms=None,
    is_staged: bool = False,
) -> None:
    """Project an entity row into FalkorDB and stamp graph_projected_at.

    Merges entity node, creates MENTIONS edge from evidence object, updates
    RELATES_TO co-occurrence edges.  Stamps entities.graph_projected_at only
    after all steps succeed.  Raises on failure.

    Staged entities (is_staged=True) are skipped entirely — no node, no edges,
    no graph_projected_at stamp.  The reconciliation cycle will project them
    once they are promoted to is_staged=FALSE.
    """
    if is_staged:
        _log.debug(
            "Graph writer: skipping staged entity entity_id=%s (is_staged=True)", entity_id
        )
        return

    label = NodeLabel.for_entity_type(entity_type)
    entity_node_id = gs.create_node(
        labels=[label],
        properties={
            "lumogis_id": entity_id,
            "name": name,
            "entity_type": entity_type,
            "user_id": user_id,
        },
    )

    source_node_id = _ensure_source_node(gs, evidence_id, evidence_type, user_id)
    if source_node_id is not None:
        gs.create_edge(
            from_id=source_node_id,
            to_id=entity_node_id,
            rel_type=EdgeType.MENTIONS,
            properties={
                "evidence_id": evidence_id,
                "evidence_type": evidence_type,
                "timestamp": _now_iso(),
                "user_id": user_id,
            },
        )

    _update_cooccurrence_edges(gs, entity_id, evidence_id, user_id)

    _log.debug(
        "Graph: entity node merged entity_id=%s type=%s evidence_id=%r",
        entity_id,
        entity_type,
        _preview(evidence_id),
    )
    _stamp_graph_projected_at("entities", "entity_id", entity_id, user_id, ms=ms)


def project_session(
    gs,
    *,
    session_id: str,
    summary: str,
    topics: list,
    entities: list,
    entity_ids: list | None = None,
    user_id: str,
    ms=None,
) -> None:
    """Project a session row into FalkorDB and stamp graph_projected_at.

    Merges Session node and creates DISCUSSED_IN edges to resolved entity
    nodes.  Stamps sessions.graph_projected_at only after success.  Raises
    on failure.
    """
    gs.create_node(
        labels=[NodeLabel.SESSION],
        properties={
            "lumogis_id": session_id,
            "user_id": user_id,
            "summary": _truncate(summary),
            "topics": topics,
            "created_at": _now_iso(),
        },
    )

    resolved_ids: list[str] = entity_ids or []
    if not resolved_ids and entities:
        resolved_ids = _resolve_entity_names(entities, user_id)

    now = _now_iso()
    for eid in resolved_ids:
        try:
            cypher = (
                "MATCH (e) WHERE e.lumogis_id = $eid AND e.user_id = $uid "
                f"MATCH (s:{NodeLabel.SESSION}) WHERE s.lumogis_id = $sid "
                f"MERGE (e)-[r:{EdgeType.DISCUSSED_IN}]->(s) "
                "SET r.timestamp = $now, r.user_id = $uid"
            )
            gs.query(
                cypher,
                {"eid": eid, "sid": session_id, "uid": user_id, "now": now},
            )
        except Exception:
            _log.exception(
                "Graph writer: DISCUSSED_IN edge failed entity_id=%s session_id=%s",
                eid,
                session_id,
            )

    _log.debug(
        "Graph: Session node merged session_id=%s discussed_in_count=%d",
        session_id,
        len(resolved_ids),
    )
    _stamp_graph_projected_at("sessions", "session_id", session_id, user_id, ms=ms)


def project_note(
    gs,
    *,
    note_id: str,
    user_id: str,
    ms=None,
) -> None:
    """Project a note row into FalkorDB and stamp graph_projected_at.

    Creates or merges the Note node only.  Entity extraction fires separate
    ENTITY_CREATED events; this helper does not re-run extraction.
    """
    gs.create_node(
        labels=[NodeLabel.NOTE],
        properties={
            "lumogis_id": note_id,
            "user_id": user_id,
            "source": "quick_capture",
            "created_at": _now_iso(),
        },
    )
    _log.debug("Graph: Note node merged note_id=%s", note_id)
    _stamp_graph_projected_at("notes", "note_id", note_id, user_id, ms=ms)


def project_audio(
    gs,
    *,
    audio_id: str,
    file_path: str,
    duration_seconds: float = 0.0,
    user_id: str,
    ms=None,
) -> None:
    """Project an audio memo row into FalkorDB and stamp graph_projected_at.

    Creates or merges the AudioMemo node only.  Entity extraction fires
    separate ENTITY_CREATED events; this helper does not re-run extraction.
    """
    gs.create_node(
        labels=[NodeLabel.AUDIO_MEMO],
        properties={
            "lumogis_id": audio_id,
            "file_path": file_path,
            "user_id": user_id,
            "duration_seconds": duration_seconds,
            "created_at": _now_iso(),
        },
    )
    _log.debug(
        "Graph: AudioMemo node merged audio_id=%s file_path=%r",
        audio_id,
        _preview(file_path),
    )
    _stamp_graph_projected_at("audio_memos", "audio_id", audio_id, user_id, ms=ms)


# ---------------------------------------------------------------------------
# Hook handlers (thin wrappers around projection helpers)
# ---------------------------------------------------------------------------

def on_document_ingested(*, file_path: str, chunk_count: int, user_id: str, **_kw) -> None:
    """Create/merge a Document node for the ingested file."""
    gs = config.get_graph_store()
    if gs is None:
        return
    try:
        project_document(gs, file_path=file_path, file_type="", user_id=user_id)
    except Exception:
        _log.exception(
            "Graph writer: DOCUMENT_INGESTED failed for file_path=%r",
            _preview(file_path),
        )


def on_entity_created(
    *,
    entity_id: str,
    name: str,
    entity_type: str,
    evidence_id: str,
    evidence_type: str,
    user_id: str,
    is_staged: bool = False,
    **_kw,
) -> None:
    """Upsert entity node, create MENTIONS edge from evidence object, update RELATES_TO.

    Staged entities (is_staged=True) are skipped entirely — project_entity returns
    immediately without writing to FalkorDB.
    """
    gs = config.get_graph_store()
    if gs is None:
        return
    try:
        project_entity(
            gs,
            entity_id=entity_id,
            entity_type=entity_type,
            name=name,
            evidence_id=evidence_id,
            evidence_type=evidence_type,
            user_id=user_id,
            is_staged=is_staged,
        )
    except Exception:
        _log.exception(
            "Graph writer: ENTITY_CREATED failed entity_id=%s type=%s",
            entity_id,
            entity_type,
        )


def on_session_ended(
    *,
    session_id: str,
    summary: str,
    topics: list,
    entities: list,
    entity_ids: list | None = None,
    user_id: str = "default",
    **_kw,
) -> None:
    """Create Session node and DISCUSSED_IN edges to resolved entity nodes."""
    gs = config.get_graph_store()
    if gs is None:
        return
    try:
        project_session(
            gs,
            session_id=session_id,
            summary=summary,
            topics=topics,
            entities=entities,
            entity_ids=entity_ids,
            user_id=user_id,
        )
    except Exception:
        _log.exception(
            "Graph writer: SESSION_ENDED failed session_id=%s", session_id
        )


def on_note_captured(*, note_id: str, user_id: str, **_kw) -> None:
    """Create a Note node. Entity extraction fires separate ENTITY_CREATED events."""
    gs = config.get_graph_store()
    if gs is None:
        return
    try:
        project_note(gs, note_id=note_id, user_id=user_id)
    except Exception:
        _log.exception("Graph writer: NOTE_CAPTURED failed note_id=%s", note_id)


def on_audio_transcribed(
    *, audio_id: str, file_path: str, duration_seconds: float = 0.0, user_id: str, **_kw
) -> None:
    """Create an AudioMemo node. Entity extraction fires separate ENTITY_CREATED events."""
    gs = config.get_graph_store()
    if gs is None:
        return
    try:
        project_audio(
            gs,
            audio_id=audio_id,
            file_path=file_path,
            duration_seconds=duration_seconds,
            user_id=user_id,
        )
    except Exception:
        _log.exception("Graph writer: AUDIO_TRANSCRIBED failed audio_id=%s", audio_id)


# ---------------------------------------------------------------------------
# ENTITY_MERGED
# ---------------------------------------------------------------------------

def on_entity_merged(*, winner_id: str, loser_id: str, user_id: str, **_kw) -> None:
    """Transfer edges from loser node to winner node (deduplicating), then delete loser."""
    gs = config.get_graph_store()
    if gs is None:
        return

    try:
        _transfer_outgoing_edges(gs, winner_id, loser_id, user_id)
        _transfer_incoming_edges(gs, winner_id, loser_id, user_id)

        cypher = (
            "MATCH (loser) WHERE loser.lumogis_id = $loser_id AND loser.user_id = $uid "
            "DETACH DELETE loser"
        )
        gs.query(cypher, {"loser_id": loser_id, "uid": user_id})
        _log.info(
            "Graph: entity merge complete winner=%s loser=%s (deleted)",
            winner_id,
            loser_id,
        )
    except Exception:
        _log.exception(
            "Graph writer: ENTITY_MERGED failed winner=%s loser=%s",
            winner_id,
            loser_id,
        )


def on_entity_merged_unknown_loser(
    *,
    winner_id: str,
    loser_lumogis_id: str,
    user_id: str,
) -> None:
    """Reconciliation-only variant of `on_entity_merged` for orphan FalkorDB nodes.

    Used by `reconcile.garbage_collect_orphan_nodes()` when an `ENTITY_MERGED`
    webhook was lost: Core's `services/entity_merge.py:apply_merge` has
    already DELETEd the loser row from Postgres (so we cannot recover the
    loser PG id), but a FalkorDB node with `lumogis_id=loser_lumogis_id`
    still exists because the projection step never ran.

    Behavior:
        - Same edge-transfer Cypher as `on_entity_merged`: any edges still
          pointing at the orphan are re-pointed at the winner before the
          orphan is deleted, so we do not lose evidence the live handler
          would have preserved.
        - Skips the loser-row Postgres DELETE step (the row is already gone).
        - Idempotent: re-running on a graph with no matching loser node is a
          no-op because `_transfer_*_edges` and the DETACH DELETE all run
          via `MATCH ... WHERE` clauses that simply find no rows.

    See the lumogis-graph extraction plan §"Webhook-loss recovery: per-event
    reconciliation mapping" for why this exists.
    """
    gs = config.get_graph_store()
    if gs is None:
        return

    try:
        _transfer_outgoing_edges(gs, winner_id, loser_lumogis_id, user_id)
        _transfer_incoming_edges(gs, winner_id, loser_lumogis_id, user_id)

        cypher = (
            "MATCH (loser) WHERE loser.lumogis_id = $loser_id AND loser.user_id = $uid "
            "DETACH DELETE loser"
        )
        gs.query(cypher, {"loser_id": loser_lumogis_id, "uid": user_id})
        _log.info(
            "Graph: orphan-node GC complete winner=%s loser_lumogis_id=%s (deleted)",
            winner_id,
            loser_lumogis_id,
        )
    except Exception:
        _log.exception(
            "Graph writer: orphan-node GC failed winner=%s loser_lumogis_id=%s",
            winner_id,
            loser_lumogis_id,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_source_node(gs, evidence_id: str, evidence_type: str, user_id: str) -> str | None:
    """Return the internal node ID of the source information-object node.

    Creates a stub node if the source does not yet exist in the graph.
    Returns None if the evidence_type is unknown.
    """
    label_map = {
        "DOCUMENT": NodeLabel.DOCUMENT,
        "SESSION": NodeLabel.SESSION,
        "NOTE": NodeLabel.NOTE,
        "AUDIO": NodeLabel.AUDIO_MEMO,
    }
    label = label_map.get(evidence_type.upper())
    if label is None:
        _log.warning(
            "Graph writer: unknown evidence_type=%r for evidence_id=%r — no source node created",
            evidence_type,
            _preview(evidence_id),
        )
        return None

    props: dict = {
        "lumogis_id": evidence_id,
        "user_id": user_id,
    }
    if label == NodeLabel.DOCUMENT:
        props["file_path"] = evidence_id

    return gs.create_node(labels=[label], properties=props)


def _update_cooccurrence_edges(
    gs, entity_id: str, evidence_id: str, user_id: str
) -> None:
    """Increment RELATES_TO co-occurrence count for entities co-mentioned in evidence_id.

    Uses canonical direction: lower lumogis_id → higher lumogis_id (lexicographic).
    Limited to MAX_COOCCURRENCE_PAIRS pairs per call to bound write amplification.
    """
    ms = config.get_metadata_store()
    max_pairs = config.get_graph_max_cooccurrence_pairs()
    try:
        siblings = ms.fetch_all(
            "SELECT DISTINCT er.source_id "
            "FROM entity_relations er "
            "INNER JOIN entities e ON e.entity_id = er.source_id "
            "WHERE er.evidence_id = %s "
            "  AND er.source_id <> %s "
            "  AND er.user_id = %s "
            "  AND (e.is_staged IS NOT TRUE) "
            "LIMIT %s",
            (evidence_id, entity_id, user_id, max_pairs),
        )
    except Exception:
        _log.exception(
            "Graph writer: failed to fetch co-occurring entities for evidence_id=%r",
            _preview(evidence_id),
        )
        return

    if len(siblings) >= max_pairs:
        _log.warning(
            "Graph writer: co-occurrence pair cap (%d) hit for evidence_id=%r — "
            "some RELATES_TO edges may be missing",
            max_pairs,
            _preview(evidence_id),
        )

    now = _now_iso()
    for row in siblings:
        sibling_id: str = row["source_id"]
        a_id, b_id = (
            (entity_id, sibling_id)
            if entity_id < sibling_id
            else (sibling_id, entity_id)
        )
        try:
            cypher = (
                "MATCH (a) WHERE a.lumogis_id = $a_id AND a.user_id = $uid "
                "MATCH (b) WHERE b.lumogis_id = $b_id AND b.user_id = $uid "
                "MERGE (a)-[r:RELATES_TO]->(b) "
                "SET r.co_occurrence_count = coalesce(r.co_occurrence_count, 0) + 1, "
                "    r.last_seen_at = $now, "
                "    r.user_id = $uid"
            )
            gs.query(cypher, {"a_id": a_id, "b_id": b_id, "uid": user_id, "now": now})
        except Exception:
            _log.exception(
                "Graph writer: RELATES_TO update failed a=%s b=%s", a_id, b_id
            )


def _resolve_entity_names(names: list[str], user_id: str) -> list[str]:
    """Fallback: resolve entity names to entity_ids via Postgres (case-insensitive)."""
    if not names:
        return []
    ms = config.get_metadata_store()
    ids: list[str] = []
    for name in names:
        try:
            # SCOPE-EXEMPT: write-side projection helper resolving the user's
            # own personal entity names → ids before merging into FalkorDB.
            # Per plan §2.11 projection writes operate on personal-scope rows
            # only; narrowed with `AND scope = 'personal'`.
            row = ms.fetch_one(
                "SELECT entity_id FROM entities "
                # SCOPE-EXEMPT: personal-only name→id lookup (see comment above).
                "WHERE user_id = %s AND scope = 'personal' "
                "  AND lower(name) = lower(%s)",
                (user_id, name),
            )
            if row:
                ids.append(row["entity_id"])
        except Exception:
            _log.exception("Graph writer: name→id fallback failed (entity name omitted)")
    return ids


def _transfer_outgoing_edges(gs, winner_id: str, loser_id: str, user_id: str) -> None:
    """Transfer outgoing edges from loser to winner, skipping duplicates."""
    rows = gs.query(
        "MATCH (loser)-[r]->(target) WHERE loser.lumogis_id = $loser_id "
        "RETURN type(r) AS rel_type, target.lumogis_id AS target_id, "
        "r.evidence_id AS evidence_id",
        {"loser_id": loser_id},
    )
    for row in rows:
        rel_type = row.get("rel_type")
        target_id = row.get("target_id")
        evidence_id = row.get("evidence_id", "")
        exists = gs.query(
            "MATCH (w)-[r]->(t) WHERE w.lumogis_id = $winner_id "
            "AND t.lumogis_id = $target_id AND type(r) = $rel_type "
            "AND r.evidence_id = $evidence_id RETURN count(r) AS cnt",
            {"winner_id": winner_id, "target_id": target_id,
             "rel_type": rel_type, "evidence_id": evidence_id},
        )
        if exists and exists[0].get("cnt", 0) > 0:
            continue
        try:
            gs.query(
                "MATCH (winner) WHERE winner.lumogis_id = $winner_id "
                "MATCH (target) WHERE target.lumogis_id = $target_id "
                f"CREATE (winner)-[:{rel_type} {{evidence_id: $evidence_id, user_id: $uid, "
                f"timestamp: $now}}]->(target)",
                {"winner_id": winner_id, "target_id": target_id,
                 "evidence_id": evidence_id, "uid": user_id, "now": _now_iso()},
            )
        except Exception:
            _log.exception(
                "Graph writer: edge transfer failed winner=%s target=%s rel=%s",
                winner_id, target_id, rel_type,
            )


def _transfer_incoming_edges(gs, winner_id: str, loser_id: str, user_id: str) -> None:
    """Transfer incoming edges to loser over to winner, skipping duplicates."""
    rows = gs.query(
        "MATCH (source)-[r]->(loser) WHERE loser.lumogis_id = $loser_id "
        "RETURN type(r) AS rel_type, source.lumogis_id AS source_id, "
        "r.evidence_id AS evidence_id",
        {"loser_id": loser_id},
    )
    for row in rows:
        rel_type = row.get("rel_type")
        source_id = row.get("source_id")
        evidence_id = row.get("evidence_id", "")
        exists = gs.query(
            "MATCH (s)-[r]->(w) WHERE s.lumogis_id = $source_id "
            "AND w.lumogis_id = $winner_id AND type(r) = $rel_type "
            "AND r.evidence_id = $evidence_id RETURN count(r) AS cnt",
            {"source_id": source_id, "winner_id": winner_id,
             "rel_type": rel_type, "evidence_id": evidence_id},
        )
        if exists and exists[0].get("cnt", 0) > 0:
            continue
        try:
            gs.query(
                "MATCH (source) WHERE source.lumogis_id = $source_id "
                "MATCH (winner) WHERE winner.lumogis_id = $winner_id "
                f"CREATE (source)-[:{rel_type} {{evidence_id: $evidence_id, user_id: $uid, "
                f"timestamp: $now}}]->(winner)",
                {"source_id": source_id, "winner_id": winner_id,
                 "evidence_id": evidence_id, "uid": user_id, "now": _now_iso()},
            )
        except Exception:
            _log.exception(
                "Graph writer: incoming edge transfer failed source=%s winner=%s rel=%s",
                source_id, winner_id, rel_type,
            )


def _stamp_graph_projected_at(
    table: str, id_col: str, id_val: str, user_id: str, ms=None
) -> None:
    """UPDATE graph_projected_at = NOW() on the source Postgres row after a successful write."""
    allowed_tables = {"entities", "file_index", "sessions", "notes", "audio_memos"}
    if table not in allowed_tables:
        _log.error("Graph writer: _stamp called with unexpected table=%r", table)
        return
    try:
        store = ms if ms is not None else config.get_metadata_store()
        store.execute(
            f"UPDATE {table} SET graph_projected_at = NOW() "  # noqa: S608 (table is allowlisted)
            f"WHERE {id_col} = %s AND user_id = %s",
            (id_val, user_id),
        )
    except Exception:
        _log.exception(
            "Graph writer: failed to stamp graph_projected_at on %s where %s=%r",
            table,
            id_col,
            id_val,
        )
