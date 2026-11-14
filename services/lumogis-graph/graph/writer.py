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
import re
import uuid
from datetime import datetime
from datetime import timezone

import config
from graph.memory_types import MEMORY_TYPE_CORRECTION
from graph.schema import EVIDENCE_TYPE_LABEL_MAP
from graph.schema import MAX_TEXT_LENGTH
from graph.schema import PERSONAL_SCOPE
from graph.schema import EdgeType
from graph.schema import NodeLabel
from graph.schema import node_scope
from graph.temporal import now_utc
from graph.temporal import parse_graph_ts
from graph.temporal import temporal_feature_enabled
from graph.temporal import to_graph_ts

_log = logging.getLogger(__name__)

_PREVIEW_LEN = 80

# Transfer-Cypher interpolation guards (entity-merge edge transfer): rel_type
# comes back from `type(r)` and property keys from `properties(r)` — both are
# graph-sourced, but guard anyway (same posture as the code-structure
# frozenset allowlist).
_SAFE_REL_TYPE = re.compile(r"^[A-Z_]+$")
_SAFE_PROP_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


def _memory_props_from_postgres(store, entity_id: str, user_id: str) -> dict[str, object]:
    try:
        row = store.fetch_one(
            "SELECT memory_type, last_verified_at FROM entities "
            "WHERE entity_id = %s AND user_id = %s",
            (entity_id, user_id),
        )
    except Exception:
        return {}
    if not row:
        return {}
    return {"memory_type": row.get("memory_type"), "last_verified_at": row.get("last_verified_at")}


def _last_verified_at_graph_string(val: object) -> str | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        ts = val if val.tzinfo else val.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = str(val).strip()
    return s if s else None


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

    The node is always ``scope='personal'`` (LUM-961) and this function has no
    ``scope`` parameter on purpose: the Document node is keyed
    ``(file_path, user_id)``, which the shared ``file_index`` projection row also
    has, so a shared row must never be able to flip the personal node to
    ``shared``.  The reconcile arm only ever passes personal rows.
    """
    ext = file_type or os.path.splitext(file_path)[1].lstrip(".").lower() or "unknown"
    node_id = gs.create_node(
        labels=[NodeLabel.DOCUMENT],
        properties={
            "lumogis_id": file_path,
            "file_path": file_path,
            "file_type": ext,
            "user_id": user_id,
            "scope": PERSONAL_SCOPE,
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
    update_cooccurrence: bool = True,
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
        _log.debug("Graph writer: skipping staged entity entity_id=%s (is_staged=True)", entity_id)
        return

    label = NodeLabel.for_entity_type(entity_type)
    store = ms or config.get_metadata_store()
    mem = _memory_props_from_postgres(store, entity_id, user_id)
    props: dict[str, object] = {
        "lumogis_id": entity_id,
        "name": name,
        "entity_type": entity_type,
        "user_id": user_id,
        # LUM-961: shared/system entity nodes are written only by
        # projection.project_entity_into_graph.
        "scope": PERSONAL_SCOPE,
    }
    mt = mem.get("memory_type")
    if isinstance(mt, str) and mt.strip():
        props["memory_type"] = mt.strip()
    lva_iso = _last_verified_at_graph_string(mem.get("last_verified_at"))
    if lva_iso:
        props["last_verified_at"] = lva_iso

    entity_node_id = gs.create_node(
        labels=[label],
        properties=props,
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
        if temporal_feature_enabled():
            # LUM-104: guarded follow-up stamp — create_edge SETs every
            # property unconditionally on re-projection, so the once-only
            # temporal properties (min-rule valid_at, created_at, edge_uid)
            # must be applied in a separate coalesce-guarded MATCH.
            _stamp_temporal_on_merged_edge(
                gs,
                from_node_id=source_node_id,
                to_node_id=entity_node_id,
                evidence_id=evidence_id,
            )

    if update_cooccurrence:
        _update_cooccurrence_edges(gs, entity_id, user_id, evidence_id)

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
    scope: str = PERSONAL_SCOPE,
    published_from: str | None = None,
    ms=None,
) -> None:
    """Project a session row into FalkorDB and stamp graph_projected_at.

    Merges Session node and creates DISCUSSED_IN edges to resolved entity
    nodes.  Stamps sessions.graph_projected_at only after success.  Raises
    on failure.  ``published_from`` is set on projection (shared/system)
    session nodes so purge can find them by source ID without a Postgres
    round-trip.

    ``scope`` is the row's scope (LUM-961): ``personal`` for the owner's own
    session, ``shared`` / ``system`` for a projection.  It is validated against
    ``published_from`` by :func:`graph.schema.node_scope` *before* any graph
    write, so an invalid combination raises ``ValueError`` and nothing is
    written.  The scope-less default is the live hook path (always personal).
    """
    node_scope(scope, published_from)
    props: dict = {
        "lumogis_id": session_id,
        "user_id": user_id,
        "scope": scope,
        "summary": _truncate(summary),
        "topics": topics,
        "created_at": _now_iso(),
    }
    if published_from is not None:
        props["published_from"] = published_from
    gs.create_node(
        labels=[NodeLabel.SESSION],
        properties=props,
    )

    resolved_ids: list[str] = entity_ids or []
    if not resolved_ids and entities:
        resolved_ids = _resolve_entity_names(entities, user_id)

    now = _now_iso()
    temporal_on = temporal_feature_enabled()
    for eid in resolved_ids:
        try:
            cypher = (
                "MATCH (e) WHERE e.lumogis_id = $eid AND e.user_id = $uid "
                f"MATCH (s:{NodeLabel.SESSION}) WHERE s.lumogis_id = $sid "
                f"MERGE (e)-[r:{EdgeType.DISCUSSED_IN}]->(s) "
                "SET r.timestamp = $now, r.user_id = $uid"
            )
            params: dict = {"eid": eid, "sid": session_id, "uid": user_id, "now": now}
            if temporal_on:
                # LUM-104: additive temporal stamps (min-rule; fixed-format
                # to_graph_ts, never _now_iso()). Flag off → byte-identical.
                cypher += _TEMPORAL_MIN_RULE_SUFFIX
                params["ts"] = to_graph_ts(now_utc())
                params["edge_uid"] = uuid.uuid4().hex
            gs.query(cypher, params)
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


def delete_session(
    gs,
    *,
    session_id: str,
    user_id: str,
) -> None:
    """Remove the personal Session node and incident edges; never delete Entity nodes.

    Edges are ``(Entity)-[:DISCUSSED_IN]->(Session)`` per ``project_session``;
    ``DETACH DELETE`` removes incident edges only.
    """
    if gs is None:
        return
    cypher = (
        f"MATCH (s:{NodeLabel.SESSION}) "
        "WHERE s.lumogis_id = $sid AND s.user_id = $uid "
        "DETACH DELETE s"
    )
    gs.query(cypher, {"sid": session_id, "uid": user_id})
    _log.debug("Graph: Session node deleted session_id=%s", session_id)


def delete_session_projections(
    gs,
    *,
    source_session_id: str,
) -> None:
    """Remove all published Session projection nodes for a personal session.

    Matches by ``published_from`` (stamped by :func:`project_session` for
    projection rows) so callers need no Postgres lookup of projection PKs.
    Never deletes Entity nodes — DETACH DELETE removes only Session nodes
    and their incident DISCUSSED_IN edges.
    """
    if gs is None:
        return
    cypher = f"MATCH (s:{NodeLabel.SESSION}) WHERE s.published_from = $src_id DETACH DELETE s"
    gs.query(cypher, {"src_id": source_session_id})
    _log.debug("Graph: Session projection nodes deleted src=%s", source_session_id)


def delete_document(
    gs,
    *,
    file_path: str,
    user_id: str,
) -> None:
    """Remove the personal Document node and incident MENTIONS edges; never delete Entity nodes."""
    if gs is None:
        return
    cypher = (
        f"MATCH (d:{NodeLabel.DOCUMENT}) "
        "WHERE d.lumogis_id = $fp AND d.user_id = $uid "
        "DETACH DELETE d"
    )
    gs.query(cypher, {"fp": file_path, "uid": user_id})
    _log.debug("Graph: Document node deleted file_path=%r", _preview(file_path))


def project_note(
    gs,
    *,
    note_id: str,
    user_id: str,
    scope: str = PERSONAL_SCOPE,
    published_from: str | None = None,
    ms=None,
) -> None:
    """Project a note row into FalkorDB and stamp graph_projected_at.

    Creates or merges the Note node only.  Entity extraction fires separate
    ENTITY_CREATED events; this helper does not re-run extraction.

    ``scope`` / ``published_from`` follow :func:`project_session` (LUM-961):
    validated first, ``published_from`` stored only on a projection node.
    """
    node_scope(scope, published_from)
    props: dict = {
        "lumogis_id": note_id,
        "user_id": user_id,
        "scope": scope,
        "source": "quick_capture",
        "created_at": _now_iso(),
    }
    if published_from is not None:
        # A shared/system projection node is not removed by unpublish (orchestrator
        # unproject_note / unproject_audio_memo / unproject_session delete Postgres +
        # Qdrant only); published_from lets the GC find it - see LUM-952 Later. Until
        # then the owner also sees a published item twice in /graph/stats (personal
        # node + projection node); no ego or mentions read follows DISCUSSED_IN to it.
        props["published_from"] = published_from
    gs.create_node(labels=[NodeLabel.NOTE], properties=props)
    _log.debug("Graph: Note node merged note_id=%s", note_id)
    _stamp_graph_projected_at("notes", "note_id", note_id, user_id, ms=ms)


def project_audio(
    gs,
    *,
    audio_id: str,
    file_path: str,
    duration_seconds: float = 0.0,
    user_id: str,
    scope: str = PERSONAL_SCOPE,
    published_from: str | None = None,
    ms=None,
) -> None:
    """Project an audio memo row into FalkorDB and stamp graph_projected_at.

    Creates or merges the AudioMemo node only.  Entity extraction fires
    separate ENTITY_CREATED events; this helper does not re-run extraction.

    ``scope`` / ``published_from`` follow :func:`project_session` (LUM-961).
    The unpublish note at :func:`project_note` applies here too.
    """
    node_scope(scope, published_from)
    props: dict = {
        "lumogis_id": audio_id,
        "file_path": file_path,
        "user_id": user_id,
        "scope": scope,
        "duration_seconds": duration_seconds,
        "created_at": _now_iso(),
    }
    if published_from is not None:
        props["published_from"] = published_from
    gs.create_node(labels=[NodeLabel.AUDIO_MEMO], properties=props)
    _log.debug(
        "Graph: AudioMemo node merged audio_id=%s file_path=%r",
        audio_id,
        _preview(file_path),
    )
    _stamp_graph_projected_at("audio_memos", "audio_id", audio_id, user_id, ms=ms)


def mark_entity_correction_on_graph(gs, *, entity_id: str, user_id: str) -> None:
    """Set FalkorDB entity node ``memory_type`` to ``correction`` when the node exists.

    Postgres remains authoritative for durable ``memory_type``; this is a projection
    seam for the LUM-114 placeholder path (feature-flagged). Idempotent SET.
    """
    cypher = (
        "MATCH (n {lumogis_id: $eid, user_id: $uid}) SET n.memory_type = $mt RETURN id(n) AS nid"
    )
    try:
        rows = gs.query(
            cypher,
            {"eid": str(entity_id), "uid": user_id, "mt": MEMORY_TYPE_CORRECTION},
        )
    except Exception:
        _log.warning(
            "mark_entity_correction_on_graph: query failed entity_id=%s user_id=%s",
            entity_id,
            user_id,
            exc_info=True,
        )
        return
    if not rows:
        _log.warning(
            "mark_entity_correction_on_graph: no graph node matched entity_id=%s user_id=%s",
            entity_id,
            user_id,
        )


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


def on_document_shared(
    *,
    file_path: str,
    user_id: str,
    entities: list,
    target_scope: str = "shared",
    **_kw,
) -> None:
    """Project a shared document's extracted entities into the shared graph (LUM-586).

    Consumes :attr:`models.webhook.WebhookEvent.DOCUMENT_SHARED`. For each
    ``{src_entity_id, proj_entity_id, name, entity_type}`` ref the orchestrator
    already committed to Postgres+Qdrant, MERGE the shared ``:Entity`` node and
    sweep incident ``RELATES_TO`` edges (``projection.project_entity_into_graph``),
    then stamp ``graph_projected_at`` on the **shared** ``entities`` row so the
    reconcile pass does not re-sweep it. A per-entity failure is left unstamped
    and retried by ``reconcile_shared_entities`` on the next pass.
    """
    gs = config.get_graph_store()
    if gs is None:
        return
    import projection as shared_projection

    ms = config.get_metadata_store()

    def _f(ent, key: str):
        # Refs arrive as plain dicts (webhook payload.model_dump()); tolerate
        # both mapping and attribute access defensively.
        return ent.get(key) if isinstance(ent, dict) else getattr(ent, key, None)

    for ent in entities or []:
        src_id = _f(ent, "src_entity_id")
        proj_id = _f(ent, "proj_entity_id")
        name = _f(ent, "name") or ""
        etype = _f(ent, "entity_type") or ""
        if not src_id or not proj_id:
            continue
        try:
            ok = shared_projection.project_entity_into_graph(
                src_entity_id=str(src_id),
                proj_entity_id=str(proj_id),
                target_scope=target_scope,
                actor_user_id=user_id,
                name=name,
                entity_type=etype,
            )
        except Exception:
            _log.exception("Graph writer: DOCUMENT_SHARED projection failed proj=%s", proj_id)
            continue
        if ok:
            _stamp_graph_projected_at("entities", "entity_id", str(proj_id), user_id, ms=ms)


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
        _log.exception("Graph writer: SESSION_ENDED failed session_id=%s", session_id)


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


def on_feedback_received(
    *,
    item_type: str,
    item_id: str,
    positive: bool | None = None,
    user_id: str = "default",
    **_kw: object,
) -> None:
    """LUM-124 placeholder: negative explicit feedback on an entity → graph ``correction`` flag."""
    del _kw
    if not config.get_memory_correction_placeholder_enabled():
        return
    if item_type != "entity" or not item_id:
        return
    if positive is not False:
        return
    gs = config.get_graph_store()
    if gs is None:
        return
    try:
        mark_entity_correction_on_graph(gs, entity_id=item_id, user_id=user_id)
    except Exception:
        _log.warning(
            "on_feedback_received: mark_entity_correction_on_graph failed entity_id=%s",
            item_id,
            exc_info=True,
        )


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

# LUM-104 temporal SET suffixes — appended to write Cypher ONLY when
# LUMOGIS_FF_TEMPORAL_KG is on (flag off ⇒ byte-identical strings, pinned by
# test). Timestamps bind $ts via graph.temporal.to_graph_ts (fixed-format
# UTC); $edge_uid is an app-side uuid4 per edge write, coalesce-guarded so an
# existing uid is never overwritten.

# Min-rule (MENTIONS / DISCUSSED_IN): valid_at only ever moves backward;
# created_at stamped once; contradiction_count initialised.
_TEMPORAL_MIN_RULE_SUFFIX = (
    ", "
    "r.valid_at = CASE WHEN r.valid_at IS NULL OR $ts < r.valid_at "
    "THEN $ts ELSE r.valid_at END, "
    "r.created_at = coalesce(r.created_at, $ts), "
    "r.edge_uid = coalesce(r.edge_uid, $edge_uid), "
    "r.contradiction_count = coalesce(r.contradiction_count, 0)"
)

# RELATES_TO recurrence rule R1: an invalidated pair that GENUINELY co-occurs
# again is a NEW validity interval — valid_at rewrites FORWARD to the episode
# time and invalid_at is removed (SET ... = NULL drops the property; the prior
# interval's history lives in SUPERSEDES). Replay guard (verify-plan fix): the
# projection layer is deliberately replayable (webhook redelivery, the daily
# reconcile re-projecting stale entities), and a replay of an episode OLDER
# than the invalidation must NOT count as recurrence — otherwise every
# reconcile pass would resurrect superseded facts. $ev_ts is the episode's
# original recording time (entity_relations.created_at, app-side normalised);
# R1 fires only when the invalidation predates it (r.invalid_at < $ev_ts).
# The CASEs read r.invalid_at before the reset (SET items apply left-to-right).
# contradiction_count is strictly increment-only: R1 does NOT reset it
# (LUM-369's stability signal).
_TEMPORAL_RELATES_TO_SUFFIX = (
    ", "
    "    r.valid_at = CASE "
    "WHEN r.invalid_at IS NOT NULL AND r.invalid_at < $ev_ts THEN $ev_ts "
    "WHEN r.invalid_at IS NOT NULL THEN r.valid_at "
    "WHEN r.valid_at IS NULL THEN $ts ELSE r.valid_at END, "
    "    r.created_at = coalesce(r.created_at, $ts), "
    "    r.edge_uid = coalesce(r.edge_uid, $edge_uid), "
    "    r.contradiction_count = coalesce(r.contradiction_count, 0), "
    "    r.invalid_at = CASE "
    "WHEN r.invalid_at IS NOT NULL AND r.invalid_at < $ev_ts THEN NULL "
    "ELSE r.invalid_at END"
)


def _evidence_recorded_ts(ms, evidence_id: str, user_id: str) -> str:
    """Fixed-format timestamp of when this episode was first recorded.

    Reads the earliest entity_relations.created_at for the evidence — stable
    across replays (webhook redelivery, reconcile re-projection), which is
    what makes the R1 replay guard sound. Falls back to now (treat as new)
    when the row is missing or unparseable.
    """
    try:
        row = ms.fetch_one(
            "SELECT min(created_at) AS created_at FROM entity_relations "
            "WHERE evidence_id = %s AND user_id = %s",
            (evidence_id, user_id),
        )
        val = (row or {}).get("created_at")
        if val is not None:
            return to_graph_ts(parse_graph_ts(str(val)))
    except Exception:
        _log.warning("Graph writer: evidence timestamp lookup failed", exc_info=True)
    return to_graph_ts(now_utc())


def _stamp_temporal_on_merged_edge(
    gs, *, from_node_id: str, to_node_id: str, evidence_id: str
) -> None:
    """Apply the temporal min-rule to a just-MERGE'd MENTIONS edge.

    MATCH-only (never MERGE): targets the single edge by endpoint internal
    ids + evidence_id, so re-projections cannot advance valid_at or rotate
    edge_uid. Never raises — a stamp failure degrades to the pre-temporal
    edge shape and the reconcile/backfill pass repairs it.
    """
    try:
        gs.query(
            f"MATCH (a)-[r:{EdgeType.MENTIONS} {{evidence_id: $eid}}]->(b) "
            "WHERE id(a) = $aid AND id(b) = $bid "
            "SET r.valid_at = CASE WHEN r.valid_at IS NULL OR $ts < r.valid_at "
            "THEN $ts ELSE r.valid_at END, "
            "r.created_at = coalesce(r.created_at, $ts), "
            "r.edge_uid = coalesce(r.edge_uid, $edge_uid), "
            "r.contradiction_count = coalesce(r.contradiction_count, 0)",
            {
                "eid": evidence_id,
                "aid": int(from_node_id),
                "bid": int(to_node_id),
                "ts": to_graph_ts(now_utc()),
                "edge_uid": uuid.uuid4().hex,
            },
        )
    except Exception:
        _log.exception(
            "Graph writer: temporal stamp failed evidence_id=%r",
            _preview(evidence_id),
        )


def _ensure_source_node(gs, evidence_id: str, evidence_type: str, user_id: str) -> str | None:
    """Return the internal node ID of the source information-object node.

    Creates a stub node if the source does not yet exist in the graph.
    Returns None if the evidence_type is unknown.

    The stub is ``scope='personal'`` (LUM-961).  ``create_node`` SETs ``scope``
    on an existing node with the same ``(lumogis_id, user_id)``, so
    ``evidence_id`` must be a personal row id, never a shared/system projection
    id.  True today: entity extraction fires only from personal capture,
    ingest and session paths, and publish (``orchestrator/services/projection.py``)
    fires no extraction hook.
    """
    label = EVIDENCE_TYPE_LABEL_MAP.get(evidence_type.upper())
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
        "scope": PERSONAL_SCOPE,
    }
    if label == NodeLabel.DOCUMENT:
        props["file_path"] = evidence_id

    return gs.create_node(labels=[label], properties=props)


def _update_cooccurrence_edges(gs, entity_id: str, user_id: str, evidence_id: str | None) -> None:
    """Write RELATES_TO co_occurrence_count = distinct shared evidence (LUM-952).

    The count for a pair is the number of distinct ``evidence_id`` values both
    entities have in ``entity_relations`` (same user, sibling not staged). It is
    recomputed from Postgres, never incremented, so webhook redelivery,
    reconcile replays and two-sided projection of the same evidence all leave it
    unchanged.

    The write is MONOTONE: it only raises the stored value. Projections run on a
    4-worker pool (``webhook_queue.py``), and two of them can read Postgres at
    different moments; a plain SET would let the older, lower snapshot win.
    Lowering an inflated edge is ``graph.reconcile.recount_cooccurrence``'s job.

    ``evidence_id`` given (webhook path): siblings are the entities co-mentioned
    in that evidence. ``None`` (reconcile, once per entity): siblings are every
    entity sharing any evidence with this one. Canonical direction is lower
    lumogis_id → higher (lexicographic); at most MAX_COOCCURRENCE_PAIRS pairs.
    """
    ms = config.get_metadata_store()
    max_pairs = config.get_graph_max_cooccurrence_pairs()
    sql = (
        "SELECT b.source_id AS sibling_id, count(DISTINCT b.evidence_id) AS shared, "
        "       max(least(a.created_at, b.created_at)) AS last_shared_at "
        "FROM entity_relations a "
        "JOIN entity_relations b ON b.evidence_id = a.evidence_id AND b.user_id = a.user_id "
        "JOIN entities e ON e.entity_id = b.source_id "
        "WHERE a.source_id = %s AND a.user_id = %s AND b.source_id <> %s "
        "  AND (e.is_staged IS NOT TRUE) "
    )
    params: tuple = (entity_id, user_id, entity_id)
    if evidence_id is not None:
        sql += (
            "  AND b.source_id IN (SELECT DISTINCT source_id FROM entity_relations "
            "                      WHERE evidence_id = %s AND user_id = %s AND source_id <> %s) "
        )
        params += (evidence_id, user_id, entity_id)
    # Deterministic under the pair cap: keep the strongest pairs, ties by id.
    sql += "GROUP BY b.source_id ORDER BY shared DESC, b.source_id LIMIT %s"
    params += (max_pairs,)
    try:
        siblings = ms.fetch_all(sql, params)
    except Exception:
        _log.exception(
            "Graph writer: failed to fetch co-occurring entities for entity_id=%s evidence_id=%r",
            entity_id,
            _preview(evidence_id) if evidence_id is not None else None,
        )
        return

    if len(siblings) >= max_pairs:
        _log.warning(
            "Graph writer: co-occurrence pair cap (%d) hit for entity_id=%s evidence_id=%r — "
            "some RELATES_TO edges may be missing",
            max_pairs,
            entity_id,
            _preview(evidence_id) if evidence_id is not None else None,
        )

    now = _now_iso()
    temporal_on = temporal_feature_enabled()
    ev_ts = (
        _evidence_recorded_ts(ms, evidence_id, user_id)
        if temporal_on and evidence_id is not None
        else None
    )
    for row in siblings:
        sibling_id: str = str(row["sibling_id"])
        count = int(row["shared"])
        a_id, b_id = (entity_id, sibling_id) if entity_id < sibling_id else (sibling_id, entity_id)
        try:
            cypher = (
                "MATCH (a) WHERE a.lumogis_id = $a_id AND a.user_id = $uid "
                "MATCH (b) WHERE b.lumogis_id = $b_id AND b.user_id = $uid "
                "MERGE (a)-[r:RELATES_TO]->(b) "
                "SET r.co_occurrence_count = CASE "
                "WHEN coalesce(r.co_occurrence_count, 0) < $count THEN $count "
                "ELSE r.co_occurrence_count END, "
                "    r.last_seen_at = $now, "
                "    r.user_id = $uid"
            )
            params_c: dict = {
                "a_id": a_id,
                "b_id": b_id,
                "uid": user_id,
                "now": now,
                "count": count,
            }
            if temporal_on:
                # LUM-104: temporal stamps + rule R1 with replay guard
                # (see suffix docstring).
                cypher += _TEMPORAL_RELATES_TO_SUFFIX
                params_c["ts"] = to_graph_ts(now_utc())
                # The R1 replay guard revives an invalidated edge only for
                # evidence recorded after the invalidation. On the reconcile
                # path (no single evidence) use the pair's latest shared
                # evidence time; never "now", which would revive every
                # invalidated edge on every pass.
                if ev_ts is not None:
                    params_c["ev_ts"] = ev_ts
                elif row.get("last_shared_at") is not None:
                    params_c["ev_ts"] = to_graph_ts(row["last_shared_at"])
                else:
                    params_c["ev_ts"] = ""
                params_c["edge_uid"] = uuid.uuid4().hex
            gs.query(cypher, params_c)
        except Exception:
            _log.exception("Graph writer: RELATES_TO update failed a=%s b=%s", a_id, b_id)


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


def _ts_pick(loser_val: object, existing_val: object, *, latest: bool) -> object:
    """Pick the earlier/later of two timestamp strings, preserving originals.

    Values may mix the fixed graph format and legacy bare isoformat, so
    lexicographic comparison is unsafe — parse through parse_graph_ts and
    return the ORIGINAL string of whichever side wins (never reformats
    non-temporal properties like last_seen_at). Unparseable values fall back
    to the existing/winner side.
    """
    if not loser_val:
        return existing_val
    if not existing_val:
        return loser_val
    try:
        p_loser = parse_graph_ts(str(loser_val))
        p_existing = parse_graph_ts(str(existing_val))
    except (ValueError, TypeError):
        return existing_val
    if latest:
        return loser_val if p_loser >= p_existing else existing_val
    return loser_val if p_loser <= p_existing else existing_val


def _reconcile_relates_to_props(loser: dict, existing: dict | None) -> dict:
    """Merge loser-edge properties into a colliding winner-pair edge.

    LUM-104 collision semantics: sum co_occurrence_count, keep the earliest
    valid_at/created_at, keep invalid_at only if BOTH edges carried it (any
    valid interval wins — mirrors rule R1's recurrence semantics), keep the
    winner-side edge_uid, keep the later last_seen_at (original string).
    contradiction_count is increment-only history — keep the max, never
    reset. Untracked keys (e.g. edge_quality): winner side wins.
    """
    if not existing:
        final = dict(loser)
        return final
    final = {**loser, **existing}
    final["co_occurrence_count"] = int(loser.get("co_occurrence_count") or 0) + int(
        existing.get("co_occurrence_count") or 0
    )
    for key in ("valid_at", "created_at"):
        picked = _ts_pick(loser.get(key), existing.get(key), latest=False)
        if picked is not None:
            final[key] = picked
        else:
            final.pop(key, None)
    if loser.get("invalid_at") and existing.get("invalid_at"):
        final["invalid_at"] = _ts_pick(
            loser.get("invalid_at"), existing.get("invalid_at"), latest=True
        )
    else:
        final.pop("invalid_at", None)
    if existing.get("edge_uid"):
        final["edge_uid"] = existing["edge_uid"]
    elif loser.get("edge_uid"):
        final["edge_uid"] = loser["edge_uid"]
    final["contradiction_count"] = max(
        int(loser.get("contradiction_count") or 0),
        int(existing.get("contradiction_count") or 0),
    )
    picked_seen = _ts_pick(loser.get("last_seen_at"), existing.get("last_seen_at"), latest=True)
    if picked_seen is not None:
        final["last_seen_at"] = picked_seen
    return final


def _transfer_relates_to_pair(
    gs, winner_id: str, other_id: str | None, loser_props: dict, user_id: str
) -> None:
    """Transfer one loser RELATES_TO edge as a canonical-pair MERGE.

    The generic transfer dedup keys on ``r.evidence_id``, which RELATES_TO
    edges never carry (``null = null`` is NULL in Cypher), so a CREATE could
    leave TWO edges for one pair — breaking the one-edge-one-interval
    invariant that edge_uid targeting, the min-rule, and R1 depend on.
    MERGE by pair (re-canonicalised lower→higher after winner substitution,
    ADR-038) + app-side property reconciliation instead.
    """
    if not other_id or other_id == winner_id:
        # A (winner, winner) self-pair has no canonical direction; the
        # loser's self-referential edge dies with the loser node.
        _log.debug("Graph writer: skipping self-pair RELATES_TO transfer")
        return
    lo, hi = (winner_id, other_id) if winner_id < other_id else (other_id, winner_id)
    try:
        existing_rows = gs.query(
            "MATCH (a)-[r:RELATES_TO]->(b) WHERE a.lumogis_id = $a_id "
            "AND b.lumogis_id = $b_id AND r.user_id = $uid "
            "RETURN properties(r) AS props",
            {"a_id": lo, "b_id": hi, "uid": user_id},
        )
        existing = dict(existing_rows[0].get("props") or {}) if existing_rows else None
        final = _reconcile_relates_to_props(dict(loser_props or {}), existing)

        set_pairs: list[str] = []
        params: dict = {"a_id": lo, "b_id": hi, "uid": user_id}
        # reserved: the query's own bindings — a graph-sourced property key
        # matching one of these must never clobber the MATCH parameters
        # (parameter-namespace injection; verify-plan security fix)
        reserved = ("user_id", "invalid_at", "a_id", "b_id", "uid")
        for key, val in final.items():
            if key in reserved or not _SAFE_PROP_KEY.match(str(key)):
                continue
            set_pairs.append(f"r.{key} = ${key}")
            params[key] = val
        set_pairs.append("r.user_id = $uid")
        # invalid_at is set explicitly or REMOVED (SET ... = NULL drops the
        # property) so a dropped interval cannot linger on the merged edge.
        if "invalid_at" in final:
            set_pairs.append("r.invalid_at = $invalid_at")
            params["invalid_at"] = final["invalid_at"]
        else:
            set_pairs.append("r.invalid_at = NULL")
        gs.query(
            "MATCH (a) WHERE a.lumogis_id = $a_id AND a.user_id = $uid "
            "MATCH (b) WHERE b.lumogis_id = $b_id AND b.user_id = $uid "
            "MERGE (a)-[r:RELATES_TO]->(b) "
            "SET " + ", ".join(set_pairs),
            params,
        )
    except Exception:
        _log.exception(
            "Graph writer: RELATES_TO pair transfer failed winner=%s other=%s",
            winner_id,
            other_id,
        )


def _create_transferred_edge(
    gs, *, from_lumogis_id: str, to_lumogis_id: str, rel_type: str, props: dict, user_id: str
) -> None:
    """CREATE a transferred edge carrying the loser edge's FULL property map.

    LUM-104: the previous fixed three-property map would strip every
    temporal property on merge — dropping invalid_at resurrects a superseded
    fact as valid, and dropping edge_uid orphans SUPERSEDES pointers.
    rel_type and property keys are regex-guarded before interpolation.
    """
    if not rel_type or not _SAFE_REL_TYPE.match(str(rel_type)):
        _log.warning("Graph writer: unsafe rel_type on edge transfer — skipped")
        return
    final = dict(props or {})
    final.setdefault("user_id", user_id)
    final.setdefault("timestamp", _now_iso())
    prop_pairs: list[str] = []
    params: dict = {"from_id": from_lumogis_id, "to_id": to_lumogis_id}
    for key, val in final.items():
        # reserved: never let a property key clobber the endpoint bindings
        # (parameter-namespace injection; verify-plan security fix)
        if key in ("from_id", "to_id") or not _SAFE_PROP_KEY.match(str(key)):
            _log.warning("Graph writer: unsafe property key on edge transfer — skipped key")
            continue
        prop_pairs.append(f"{key}: ${key}")
        params[key] = val
    gs.query(
        "MATCH (from_node) WHERE from_node.lumogis_id = $from_id "
        "MATCH (to_node) WHERE to_node.lumogis_id = $to_id "
        f"CREATE (from_node)-[:{rel_type} {{{', '.join(prop_pairs)}}}]->(to_node)",
        params,
    )


def _transfer_outgoing_edges(gs, winner_id: str, loser_id: str, user_id: str) -> None:
    """Transfer outgoing edges from loser to winner, skipping duplicates.

    Copies the full source-edge property map (LUM-104 — see
    ``_create_transferred_edge``); RELATES_TO is MERGE'd by canonical pair
    with reconciliation (see ``_transfer_relates_to_pair``).
    """
    rows = gs.query(
        "MATCH (loser)-[r]->(target) WHERE loser.lumogis_id = $loser_id "
        "RETURN type(r) AS rel_type, target.lumogis_id AS target_id, "
        "properties(r) AS props",
        {"loser_id": loser_id},
    )
    for row in rows:
        rel_type = row.get("rel_type")
        target_id = row.get("target_id")
        props = dict(row.get("props") or {})
        if rel_type == EdgeType.RELATES_TO:
            _transfer_relates_to_pair(gs, winner_id, target_id, props, user_id)
            continue
        evidence_id = props.get("evidence_id", "")
        exists = gs.query(
            "MATCH (w)-[r]->(t) WHERE w.lumogis_id = $winner_id "
            "AND t.lumogis_id = $target_id AND type(r) = $rel_type "
            "AND r.evidence_id = $evidence_id RETURN count(r) AS cnt",
            {
                "winner_id": winner_id,
                "target_id": target_id,
                "rel_type": rel_type,
                "evidence_id": evidence_id,
            },
        )
        if exists and exists[0].get("cnt", 0) > 0:
            continue
        try:
            _create_transferred_edge(
                gs,
                from_lumogis_id=winner_id,
                to_lumogis_id=target_id,
                rel_type=rel_type,
                props=props,
                user_id=user_id,
            )
        except Exception:
            _log.exception(
                "Graph writer: edge transfer failed winner=%s target=%s rel=%s",
                winner_id,
                target_id,
                rel_type,
            )


def _transfer_incoming_edges(gs, winner_id: str, loser_id: str, user_id: str) -> None:
    """Transfer incoming edges to loser over to winner, skipping duplicates.

    Same full-property-map + RELATES_TO pair semantics as
    ``_transfer_outgoing_edges``.
    """
    rows = gs.query(
        "MATCH (source)-[r]->(loser) WHERE loser.lumogis_id = $loser_id "
        "RETURN type(r) AS rel_type, source.lumogis_id AS source_id, "
        "properties(r) AS props",
        {"loser_id": loser_id},
    )
    for row in rows:
        rel_type = row.get("rel_type")
        source_id = row.get("source_id")
        props = dict(row.get("props") or {})
        if rel_type == EdgeType.RELATES_TO:
            _transfer_relates_to_pair(gs, winner_id, source_id, props, user_id)
            continue
        evidence_id = props.get("evidence_id", "")
        exists = gs.query(
            "MATCH (s)-[r]->(w) WHERE s.lumogis_id = $source_id "
            "AND w.lumogis_id = $winner_id AND type(r) = $rel_type "
            "AND r.evidence_id = $evidence_id RETURN count(r) AS cnt",
            {
                "source_id": source_id,
                "winner_id": winner_id,
                "rel_type": rel_type,
                "evidence_id": evidence_id,
            },
        )
        if exists and exists[0].get("cnt", 0) > 0:
            continue
        try:
            _create_transferred_edge(
                gs,
                from_lumogis_id=source_id,
                to_lumogis_id=winner_id,
                rel_type=rel_type,
                props=props,
                user_id=user_id,
            )
        except Exception:
            _log.exception(
                "Graph writer: incoming edge transfer failed source=%s winner=%s rel=%s",
                source_id,
                winner_id,
                rel_type,
            )


def _stamp_graph_projected_at(table: str, id_col: str, id_val: str, user_id: str, ms=None) -> None:
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


# --------------------------------------------------------------------------- #
# Code-structure projection (LUM-301)
# --------------------------------------------------------------------------- #
# Graph-internal provenance tag for structure-ingested rows (NOT a Postgres
# evidence_type / MCP evidence kind). Mirrors codegraph.models.
_CODE_STRUCTURE_EVIDENCE_TYPE = "CODE_STRUCTURE"

# Cypher-charset allowlist (^[A-Z_]+$) — rel_type is interpolated into the MERGE
# (Cypher cannot bind a relationship type as a parameter), so this frozenset is
# the injection control, enforced before any Cypher runs.
_CODE_STRUCTURE_RELS = frozenset({"CALLS", "DEPENDS_ON", "IMPLEMENTS"})


def project_code_structure(
    gs,
    *,
    components,
    edges,
    bank: str,
    user_id: str,
    ingest_run_id: str,
) -> tuple[int, int]:
    """Project code-structure Component/Library nodes and structural edges.

    Nodes MERGE on ``(lumogis_id, user_id)`` via ``create_node``. Edges MERGE on
    ``(a, b, rel_type, evidence_id)`` matched by ``lumogis_id`` (entity↔entity,
    not the document→MENTIONS→entity provenance pattern). Run-level properties
    (``user_id``, ``bank``, ``ingest_run_id``, ``evidence_type``, ``timestamp``)
    are stamped here. Returns ``(nodes_written, edges_written)``.

    Call order: project all nodes first, then edges, so cross-file edge
    endpoints (both matched by lumogis_id) exist before the MERGE.
    """
    now = _now_iso()
    nodes_written = 0
    for c in components:
        props = {k: v for k, v in (c.props or {}).items() if v is not None}
        props.update(
            {
                "lumogis_id": c.lumogis_id,
                "user_id": user_id,
                "bank": bank,
                "ingest_run_id": ingest_run_id,
                "evidence_type": _CODE_STRUCTURE_EVIDENCE_TYPE,
            }
        )
        # LUM-961: last, so a parser-supplied c.props["scope"] cannot override it.
        props["scope"] = PERSONAL_SCOPE
        gs.create_node(labels=[c.label], properties=props)
        nodes_written += 1

    edges_written = 0
    for e in edges:
        rel = e.rel_type
        if rel not in _CODE_STRUCTURE_RELS:
            raise ValueError(f"illegal code-structure rel_type {rel!r}")
        cypher = (
            "MATCH (a) WHERE a.lumogis_id = $from_id AND a.user_id = $uid "
            "MATCH (b) WHERE b.lumogis_id = $to_id AND b.user_id = $uid "
            f"MERGE (a)-[r:{rel} {{evidence_id: $eid}}]->(b) "
            "SET r.timestamp = $now, r.user_id = $uid, "
            "    r.ingest_run_id = $run, r.evidence_type = $etype "
            "RETURN 1 AS ok"
        )
        rows = gs.query(
            cypher,
            {
                "from_id": e.from_id,
                "to_id": e.to_id,
                "uid": user_id,
                "eid": e.evidence_id,
                "now": now,
                "run": ingest_run_id,
                "etype": _CODE_STRUCTURE_EVIDENCE_TYPE,
            },
        )
        edges_written += len(rows or [])  # 0 when an endpoint node is absent

    _log.debug(
        "Graph: code-structure projected nodes=%d edges=%d bank=%s run=%s",
        nodes_written,
        edges_written,
        bank,
        ingest_run_id,
    )
    return nodes_written, edges_written


def sweep_code_structure(gs, *, user_id: str, files: list[str], ingest_run_id: str) -> int:
    """Delete prior-run CODE_STRUCTURE Component nodes for the given files.

    Write-new-then-sweep-old: removes file-scoped nodes whose ``ingest_run_id``
    differs from the current run (DETACH DELETE also drops incident structural
    edges). Library nodes (no ``file`` prop, shared across files) are MERGEd and
    not file-swept; stale unreferenced libraries are harmless and GC'able later.
    Returns the number of files swept. Never raises — logs and continues.
    """
    swept = 0
    for file_rel in files:
        try:
            gs.query(
                "MATCH (n) WHERE n.user_id = $uid "
                "AND n.evidence_type = $etype AND n.file = $file "
                "AND n.ingest_run_id <> $run DETACH DELETE n",
                {
                    "uid": user_id,
                    "etype": _CODE_STRUCTURE_EVIDENCE_TYPE,
                    "file": file_rel,
                    "run": ingest_run_id,
                },
            )
            swept += 1
        except Exception:
            _log.warning("codegraph_sweep_failed file=%s run=%s", file_rel, ingest_run_id)
    return swept
