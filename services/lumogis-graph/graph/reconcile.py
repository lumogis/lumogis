# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Graph reconciliation: replay stale Postgres rows into FalkorDB.

Stale detection
---------------
A row is stale when:
  graph_projected_at IS NULL       — never projected
  OR updated_at > graph_projected_at  — projected but Postgres row changed since

For tables without an updated_at trigger (e.g. file_index where updates are
handled by ingest.py explicitly), the same condition holds because ingest.py
sets updated_at=NOW() on every re-ingest.

Projection logic
----------------
All graph writes are delegated to the shared helpers in writer.py:
  project_document, project_entity, project_session, project_note, project_audio

This guarantees that reconciliation and live hook handlers use identical
graph-write code.

Entity reconciliation note
--------------------------
entity_relations holds one row per (entity_id, evidence_id) pair. To replay
the full ENTITY_CREATED projection unit for an entity, we need to know which
evidence source was used. We replay ALL entity_relations rows for that entity
(one call per relation row), each of which stamps the same entity node and
creates/updates the corresponding MENTIONS and RELATES_TO edges. The
graph_projected_at stamp on entities is written after each per-relation replay.
Graph writes are idempotent: nodes and edges are MERGEd, and RELATES_TO
co_occurrence_count is written from the distinct shared-evidence count in
Postgres, never incremented (LUM-952), so replays change nothing. The orphan
GC is guarded against mass deletion of personal nodes (see
garbage_collect_orphan_nodes).

Log policy
----------
NEVER log raw user text. IDs, counts, and safe previews only.
"""

import logging
import os
import re
import time
import uuid
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import config
from graph.schema import EVIDENCE_TYPE_LABEL_MAP
from graph.schema import PERSONAL_SCOPE
from graph.schema import TEMPORAL_EDGE_TYPES
from graph.schema import NodeLabel
from graph.temporal import STATUS_BACKFILL
from graph.temporal import now_utc
from graph.temporal import parse_graph_ts
from graph.temporal import temporal_feature_enabled
from graph.temporal import to_graph_ts

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Counter dataclass (plain dict for zero-dep simplicity)
# ---------------------------------------------------------------------------


def _empty_counter(projection_type: str) -> dict:
    return {
        "projection_type": projection_type,
        "scanned": 0,
        "projected_ok": 0,
        "projected_failed": 0,
        "stamped": 0,
        "duration_ms": 0,
    }


def _log_counter(c: dict) -> None:
    _log.info(
        "component=graph_reconcile projection_type=%s scanned=%d projected_ok=%d "
        "projected_failed=%d stamped=%d duration_ms=%d",
        c["projection_type"],
        c["scanned"],
        c["projected_ok"],
        c["projected_failed"],
        c["stamped"],
        c["duration_ms"],
    )


# ---------------------------------------------------------------------------
# Stale-row queries
# ---------------------------------------------------------------------------

_STALE_CONDITION = "graph_projected_at IS NULL OR updated_at > graph_projected_at"

# file_index has no updated_at trigger — but ingest.py writes updated_at=NOW()
# on every ingest, so the same condition is correct.
_STALE_CONDITION_FILE_INDEX = _STALE_CONDITION


def _stale_limit_clause(limit: int | None) -> str:
    return f"LIMIT {int(limit)}" if limit is not None else ""


def _pf(row: dict) -> str | None:
    """``published_from`` of a Postgres row as a string, or None when absent/NULL.

    The column is a UUID; psycopg2 returns ``str`` today (no ``register_uuid``),
    but a FalkorDB param must never receive a ``uuid.UUID`` and
    ``delete_session_projections`` matches by string equality (LUM-961).
    """
    val = row.get("published_from")
    return None if val is None else str(val)


# ---------------------------------------------------------------------------
# Per-type reconciliation functions
# ---------------------------------------------------------------------------


def reconcile_documents(limit: int | None = None) -> dict:
    """Reconcile stale file_index rows into FalkorDB Document nodes."""
    counter = _empty_counter("document")
    t0 = time.monotonic()

    gs = config.get_graph_store()
    if gs is None:
        _log.debug("Graph reconcile: graph store unavailable — skipping documents")
        counter["duration_ms"] = int((time.monotonic() - t0) * 1000)
        return counter

    ms = config.get_metadata_store()
    lim = _stale_limit_clause(limit)
    # LUM-961: personal rows only. The shared projection row shares
    # (file_path, user_id) with the personal Document node; projecting it would
    # be meaningless and, if a writer ever took scope from the row, would expose
    # the personal node. Shared documents reach the graph via the LUM-586 entity
    # cascade. A shared row keeps graph_projected_at NULL until the personal row
    # is next projected; nothing reads it. (file_index.scope is NOT NULL, so no
    # NULL branch.)
    rows = ms.fetch_all(
        f"SELECT file_path, file_type, user_id FROM file_index "  # noqa: S608
        f"WHERE ({_STALE_CONDITION_FILE_INDEX}) AND (scope = 'personal') {lim}"
    )
    counter["scanned"] = len(rows)

    from graph.writer import project_document

    for row in rows:
        try:
            project_document(
                gs,
                file_path=row["file_path"],
                file_type=row.get("file_type", ""),
                user_id=row["user_id"],
                ms=ms,
            )
            counter["projected_ok"] += 1
            counter["stamped"] += 1
        except Exception:
            _log.exception(
                "Graph reconcile: document projection failed file_path=%r",
                row["file_path"][:80],
            )
            counter["projected_failed"] += 1

    counter["duration_ms"] = int((time.monotonic() - t0) * 1000)
    _log_counter(counter)
    return counter


# Edge types the typed-edge prune must never touch: aggregate/derived edges
# written by other writers, and the code-structure edges. Grow this with
# ``graph/schema.py`` (LUM-965).
_PRUNE_PROTECTED_EDGE_TYPES = frozenset(
    {
        "MENTIONS",
        "RELATES_TO",
        "DISCUSSED_IN",
        "DERIVED_FROM",
        "LINKS_TO",
        "TAGGED_WITH",
        "SUPERSEDES",
        "CALLS",
        "DEPENDS_ON",
        "IMPLEMENTS",
    }
)


def _default_graph_bank() -> str:
    """The bank ``config.get_graph_store()`` resolves with no argument.

    The graph service's ``config`` has the resolver; Core's (in-process builds)
    does not, so the same rule is read from the same environment variable.
    """
    resolver = getattr(config, "_resolve_graph_bank", None)
    if resolver is not None:
        return resolver(None)
    raw = os.environ.get("LUMOGIS_GRAPH_DEFAULT_BANK", "personal").strip() or "personal"
    return raw if raw in {"coding", "personal", "default"} else "personal"


def _entity_is_merge_involved(ms, entity_id: str, user_id: str) -> bool:
    """True when ``entity_id`` is named in one of the member's ``entity_merges`` rows.

    Every case that needs the prune involves such an entry: an undo (both sides),
    a mention move offered after an undo, and a lost ``ENTITY_MERGED`` webhook (the
    survivor). Renames, kind changes and new entries never do, so they pay one
    indexed read and no Cypher. A failing read (for example an old schema without
    the table) skips the prune.
    """
    try:
        row = ms.fetch_one(
            "SELECT 1 AS x FROM entity_merges "
            # SCOPE-EXEMPT: entity_merges is per-member (no scope column); user_id-bound.
            "WHERE user_id = %s AND (winner_id = %s::uuid OR loser_id = %s::uuid) LIMIT 1",
            (user_id, str(entity_id), str(entity_id)),
        )
    except Exception:
        _log.debug("Graph reconcile: merge-involvement read failed entity_id=%s", entity_id)
        return False
    return row is not None


def _prune_merged_entity(
    gs, ms, *, entity_id: str, entity_type: str, user_id: str, keep_evidence: list[str]
) -> None:
    """Delete graph edges of a re-projected entry that Postgres no longer backs (LUM-965).

    Replay (``project_entity``) only ever adds. After an undo the survivor still
    carries MENTIONS, DISCUSSED_IN, typed and co-occurrence edges that now belong
    to the restored entry, so for a merge-involved personal entry these four are
    a function of Postgres:

    * (a) ``MENTIONS`` whose ``evidence_id`` is not in ``keep_evidence`` (the
      replay list itself, including the synthetic ``ENTITY`` stub, so the prune
      and the replay can never disagree);
    * (b) ``DISCUSSED_IN`` to sessions whose non-empty ``entity_ids`` no longer
      name the entry. A session with empty ``entity_ids`` is never pruned:
      ``project_session`` resolves its edges by name, so Postgres has no id-level
      truth for it. Known residual: a legacy session's name-resolved edge that
      pointed at the survivor for the loser's name stays after undo;
    * (c) typed entity→entity edges (the ``entity_edges`` relation types, minus
      the protected set) with no backing active row in this graph's bank;
    * (d) zero-share ``RELATES_TO`` co-occurrence edges. Non-zero counts that the
      merge inflated stay until ``recount_cooccurrence`` (the LUM-952 monotone
      rule); typed ``RELATES_TO`` edges carry no count and are never touched.

    MENTIONS / DISCUSSED_IN writers (checked at plan time): the only MENTIONS
    writer onto an entity node is ``graph/writer.py::project_entity``; the only
    DISCUSSED_IN writer is ``project_session``. A new writer of an edge onto an
    Entity node without a Postgres row would be pruned: list it here.

    Every statement names the entity label and fixed edge types in its text;
    ids and keep-lists travel as parameters. FalkorDB has no indexes here, so an
    unlabelled match would scan every node. Raises on any failure; the caller
    unstamps the row so the next pass retries.
    """
    from graph.writer import _cooccurrence_siblings

    label = NodeLabel.for_entity_type(entity_type or "")
    if label not in NodeLabel.ENTITY_LABELS:
        return
    p = {"eid": str(entity_id), "uid": user_id}

    # (a) MENTIONS
    gs.query(
        f"MATCH (e:{label} {{lumogis_id: $eid, user_id: $uid}})<-[r:MENTIONS]-(s) "
        "WHERE NOT r.evidence_id IN $keep DELETE r",
        {**p, "keep": [str(k) for k in keep_evidence]},
    )

    # (b) DISCUSSED_IN: read the graph's sessions, decide in Postgres, delete.
    rows = gs.query(
        f"MATCH (e:{label} {{lumogis_id: $eid, user_id: $uid}})-[:DISCUSSED_IN]->(s:Session) "
        "RETURN s.lumogis_id AS sid",
        p,
    )
    graph_sessions = [str(r.get("sid") or r.get("_col0")) for r in rows if r]
    if graph_sessions:
        stale = ms.fetch_all(
            "SELECT session_id::text AS sid FROM sessions "
            # SCOPE-EXEMPT: the owning member's own session rows (user_id-bound).
            "WHERE user_id = %s AND session_id::text = ANY(%s) "
            "  AND coalesce(cardinality(entity_ids), 0) > 0 AND NOT (%s = ANY(entity_ids))",
            (user_id, graph_sessions, str(entity_id)),
        )
        prune = [str(r["sid"]) for r in stale]
        if prune:
            gs.query(
                f"MATCH (e:{label} {{lumogis_id: $eid, user_id: $uid}})"
                "-[r:DISCUSSED_IN]->(s:Session) "
                "WHERE s.lumogis_id IN $prune DELETE r",
                {**p, "prune": prune},
            )

    # (c) typed entity edges. ``reconcile_entities`` uses the default store, so the
    # bank is the one that store resolves (edges of other banks live on other graphs).
    bank = _default_graph_bank()
    type_rows = ms.fetch_all(
        "SELECT DISTINCT relation_type FROM entity_edges "
        # SCOPE-EXEMPT: entity_edges is per-member (no scope column); user_id-bound.
        "WHERE user_id = %s AND bank = %s AND relation_type <> 'RELATES_TO'",
        (user_id, bank),
    )
    typed = sorted(
        {r["relation_type"] for r in type_rows} - _PRUNE_PROTECTED_EDGE_TYPES,
    )
    if typed:
        active_rows = ms.fetch_all(
            "SELECT src_entity_id, dst_entity_id, relation_type FROM entity_edges "
            # SCOPE-EXEMPT: entity_edges is per-member (no scope column); user_id-bound.
            "WHERE user_id = %s AND bank = %s AND valid_until IS NULL "
            "  AND (src_entity_id = %s OR dst_entity_id = %s)",
            (user_id, bank, str(entity_id), str(entity_id)),
        )
        active = [
            f"{r['src_entity_id']}|{r['dst_entity_id']}|{r['relation_type']}" for r in active_rows
        ]
        gs.query(
            f"MATCH (e:{label} {{lumogis_id: $eid, user_id: $uid}})-[r]-(o) "
            "WHERE type(r) IN $typed "
            "  AND NOT (startNode(r).lumogis_id + '|' + endNode(r).lumogis_id + '|' + type(r)) "
            "      IN $active "
            "DELETE r",
            {**p, "typed": typed, "active": active},
        )

    # (d) zero-share co-occurrence edges (skipped when the sibling list is capped).
    siblings, capped = _cooccurrence_siblings(ms, str(entity_id), user_id, None)
    if not capped:
        gs.query(
            f"MATCH (e:{label} {{lumogis_id: $eid, user_id: $uid}})-[r:RELATES_TO]-(o) "
            "WHERE r.co_occurrence_count IS NOT NULL "
            "  AND (o.scope IS NULL OR o.scope = 'personal') "
            "  AND NOT o.lumogis_id IN $siblings DELETE r",
            {**p, "siblings": [str(r["sibling_id"]) for r in siblings]},
        )


def reconcile_entities(limit: int | None = None) -> dict:
    """Reconcile stale entities rows into FalkorDB entity nodes + edges.

    For each stale entity, replays all entity_relations rows so that every
    MENTIONS edge and RELATES_TO co-occurrence is (re-)created.
    """
    counter = _empty_counter("entity")
    t0 = time.monotonic()

    gs = config.get_graph_store()
    if gs is None:
        _log.debug("Graph reconcile: graph store unavailable — skipping entities")
        counter["duration_ms"] = int((time.monotonic() - t0) * 1000)
        return counter

    ms = config.get_metadata_store()
    lim = _stale_limit_clause(limit)
    # LUM-586: personal-scope arm ONLY. Without this filter the personal writer
    # (graph/writer.py::project_entity) would (mis-)project scope='shared' rows
    # as type-labelled personal nodes and stamp graph_projected_at, starving the
    # shared-scope arm (reconcile_shared_entities) below of its stale rows.
    stale = ms.fetch_all(
        f"SELECT entity_id, name, entity_type, user_id "  # noqa: S608
        f"FROM entities WHERE ({_STALE_CONDITION}) AND (is_staged IS NOT TRUE) "
        f"AND (scope = 'personal' OR scope IS NULL) {lim}"
    )
    counter["scanned"] = len(stale)

    from graph.writer import _update_cooccurrence_edges
    from graph.writer import project_entity

    for entity_row in stale:
        entity_id = entity_row["entity_id"]
        entity_type = entity_row["entity_type"]
        name = entity_row["name"]
        user_id = entity_row["user_id"]

        # Fetch all evidence relations for this entity
        relations = ms.fetch_all(
            "SELECT evidence_id, evidence_type FROM entity_relations "
            "WHERE source_id = %s AND user_id = %s",
            (entity_id, user_id),
        )

        has_evidence = bool(relations)
        if not relations:
            # Entity exists in Postgres but has no relations yet — project with
            # a synthetic stub so the node exists for future edge creation.
            relations = [{"evidence_id": entity_id, "evidence_type": "ENTITY"}]

        entity_ok = True
        for idx, rel in enumerate(relations):
            try:
                project_entity(
                    gs,
                    entity_id=entity_id,
                    entity_type=entity_type,
                    name=name,
                    evidence_id=rel["evidence_id"],
                    evidence_type=rel["evidence_type"],
                    user_id=user_id,
                    ms=ms,
                    is_staged=False,
                    # LUM-952: co-occurrence is computed once per entity below,
                    # not once per evidence row (that was quadratic in the
                    # entity's evidence count).
                    update_cooccurrence=False,
                    # LUM-967: relabel the node once per entity per pass, not
                    # once per evidence row.
                    converge_label=(idx == 0),
                )
            except Exception:
                _log.exception(
                    "Graph reconcile: entity projection failed entity_id=%s type=%s",
                    entity_id,
                    entity_type,
                )
                entity_ok = False
                if idx > 0:
                    # LUM-54: project_entity stamps graph_projected_at after
                    # EACH relation, so the relations replayed before this one
                    # have already stamped the row. Left stamped, the row is no
                    # longer stale and the remaining relations are never
                    # retried. Clear the stamp so the next pass replays the
                    # whole entity, as for any other stale row. Best-effort.
                    try:
                        ms.execute(
                            "UPDATE entities SET graph_projected_at = NULL "
                            "WHERE entity_id = %s AND user_id = %s",
                            (entity_id, user_id),
                        )
                    except Exception:
                        _log.warning(
                            "Graph reconcile: entity unstamp failed entity_id=%s", entity_id
                        )
                break  # stop replaying relations for this entity; leave unstamped

        prune_failed = False
        if entity_ok and _entity_is_merge_involved(ms, entity_id, user_id):
            # LUM-965: replay only adds. Take away what Postgres no longer backs.
            try:
                _prune_merged_entity(
                    gs,
                    ms,
                    entity_id=entity_id,
                    entity_type=entity_type,
                    user_id=user_id,
                    keep_evidence=[rel["evidence_id"] for rel in relations],
                )
            except Exception:
                _log.warning(
                    "Graph reconcile: prune failed entity_id=%s — will retry",
                    entity_id,
                    exc_info=True,
                )
                counter["prune_failed"] = counter.get("prune_failed", 0) + 1
                prune_failed = True
                try:
                    # Same recovery as LUM-54: clear the stamp so the next pass
                    # replays and prunes again.
                    ms.execute(
                        "UPDATE entities SET graph_projected_at = NULL "
                        "WHERE entity_id = %s AND user_id = %s",
                        (entity_id, user_id),
                    )
                except Exception:
                    _log.warning("Graph reconcile: prune unstamp failed entity_id=%s", entity_id)

        if entity_ok and has_evidence:
            # One aggregate over every entity sharing any evidence with this one.
            # Failures are logged inside and never unstamp the row: the nodes and
            # MENTIONS edges are in place. A pair missed this way is written by the
            # next projection that involves either endpoint (new evidence, a
            # webhook redelivery) or by POST /kg/recount-cooccurrence — not by the
            # next reconcile, since the row is now stamped.
            _update_cooccurrence_edges(gs, entity_id, user_id, None)
        if entity_ok:
            counter["projected_ok"] += 1
            if not prune_failed:  # a failed prune left the row unstamped
                counter["stamped"] += 1
        else:
            counter["projected_failed"] += 1

    counter["duration_ms"] = int((time.monotonic() - t0) * 1000)
    _log_counter(counter)
    return counter


def reconcile_shared_entities(limit: int | None = None) -> dict:
    """Reconcile stale ``scope='shared'`` entity projections into the shared graph (LUM-586).

    The live path is the ``DOCUMENT_SHARED`` webhook
    (``graph.writer.on_document_shared`` → ``projection.project_entity_into_graph``);
    this pass is the reconcile backstop for dropped webhooks and re-shares.

    Stale detection uses the full ``_STALE_CONDITION``
    (``graph_projected_at IS NULL OR updated_at > graph_projected_at``): a dual
    share (LUM-581 direct + LUM-586 cascade) advances ``updated_at`` via the
    ``project_entity`` ``ON CONFLICT`` without re-stamping ``graph_projected_at``,
    so a bare ``IS NULL`` scan would never re-sweep such a conflict-updated row.

    Each row MERGEs the shared node and sweeps incident ``RELATES_TO`` edges via
    the same primitive the webhook uses; ``graph_projected_at`` is stamped only
    on success so a backend failure is retried next pass.
    """
    counter = _empty_counter("shared_entity")
    t0 = time.monotonic()

    gs = config.get_graph_store()
    if gs is None:
        _log.debug("Graph reconcile: graph store unavailable — skipping shared entities")
        counter["duration_ms"] = int((time.monotonic() - t0) * 1000)
        return counter

    ms = config.get_metadata_store()
    lim = _stale_limit_clause(limit)
    stale = ms.fetch_all(
        f"SELECT entity_id, published_from, name, entity_type, user_id "  # noqa: S608
        f"FROM entities WHERE scope = 'shared' AND published_from IS NOT NULL "
        f"AND ({_STALE_CONDITION}) {lim}"
    )
    counter["scanned"] = len(stale)

    import projection as shared_projection

    for row in stale:
        proj_entity_id = row["entity_id"]
        src_entity_id = row.get("published_from")
        user_id = row["user_id"]
        if not src_entity_id:
            continue
        try:
            ok = shared_projection.project_entity_into_graph(
                src_entity_id=str(src_entity_id),
                proj_entity_id=str(proj_entity_id),
                target_scope="shared",
                actor_user_id=user_id,
                name=row.get("name") or "",
                entity_type=row.get("entity_type") or "",
            )
        except Exception:
            _log.exception(
                "Graph reconcile: shared entity projection failed entity_id=%s",
                proj_entity_id,
            )
            counter["projected_failed"] += 1
            continue
        if not ok:
            counter["projected_failed"] += 1
            continue
        counter["projected_ok"] += 1
        try:
            ms.execute(
                "UPDATE entities SET graph_projected_at = NOW() "
                "WHERE entity_id = %s AND user_id = %s",
                (proj_entity_id, user_id),
            )
            counter["stamped"] += 1
        except Exception:
            _log.warning("Graph reconcile: shared entity stamp failed entity_id=%s", proj_entity_id)

    counter["duration_ms"] = int((time.monotonic() - t0) * 1000)
    _log_counter(counter)
    return counter


def reconcile_sessions(limit: int | None = None) -> dict:
    """Reconcile stale sessions rows into FalkorDB Session nodes + DISCUSSED_IN edges."""
    counter = _empty_counter("session")
    t0 = time.monotonic()

    gs = config.get_graph_store()
    if gs is None:
        _log.debug("Graph reconcile: graph store unavailable — skipping sessions")
        counter["duration_ms"] = int((time.monotonic() - t0) * 1000)
        return counter

    ms = config.get_metadata_store()
    lim = _stale_limit_clause(limit)
    rows = ms.fetch_all(
        f"SELECT session_id, summary, topics, entities, entity_ids, user_id, scope, "  # noqa: S608
        f"published_from "
        f"FROM sessions WHERE {_STALE_CONDITION} {lim}"
    )
    counter["scanned"] = len(rows)

    from graph.writer import project_session

    for row in rows:
        # Prefer UUID-based entity_ids (persisted since M2 close-out).
        # Fall back to name-string resolution for historical rows where the
        # column is empty (sessions recorded before this change was deployed).
        row_entity_ids = row.get("entity_ids") or []
        try:
            project_session(
                gs,
                session_id=row["session_id"],
                summary=row.get("summary", ""),
                topics=row.get("topics") or [],
                entities=row.get("entities") or [],
                entity_ids=row_entity_ids if row_entity_ids else None,
                user_id=row["user_id"],
                scope=row.get("scope", PERSONAL_SCOPE),
                published_from=_pf(row),
                ms=ms,
            )
            counter["projected_ok"] += 1
            counter["stamped"] += 1
        except Exception:
            _log.exception(
                "Graph reconcile: session projection failed session_id=%s",
                row["session_id"],
            )
            counter["projected_failed"] += 1

    counter["duration_ms"] = int((time.monotonic() - t0) * 1000)
    _log_counter(counter)
    return counter


def reconcile_notes(limit: int | None = None) -> dict:
    """Reconcile stale notes rows into FalkorDB Note nodes."""
    counter = _empty_counter("note")
    t0 = time.monotonic()

    gs = config.get_graph_store()
    if gs is None:
        _log.debug("Graph reconcile: graph store unavailable — skipping notes")
        counter["duration_ms"] = int((time.monotonic() - t0) * 1000)
        return counter

    ms = config.get_metadata_store()
    lim = _stale_limit_clause(limit)
    rows = ms.fetch_all(
        f"SELECT note_id, user_id, scope, published_from FROM notes "  # noqa: S608
        f"WHERE {_STALE_CONDITION} {lim}"
    )
    counter["scanned"] = len(rows)

    from graph.writer import project_note

    for row in rows:
        try:
            project_note(
                gs,
                note_id=row["note_id"],
                user_id=row["user_id"],
                scope=row.get("scope", PERSONAL_SCOPE),
                published_from=_pf(row),
                ms=ms,
            )
            counter["projected_ok"] += 1
            counter["stamped"] += 1
        except Exception:
            _log.exception("Graph reconcile: note projection failed note_id=%s", row["note_id"])
            counter["projected_failed"] += 1

    counter["duration_ms"] = int((time.monotonic() - t0) * 1000)
    _log_counter(counter)
    return counter


def reconcile_audio(limit: int | None = None) -> dict:
    """Reconcile stale audio_memos rows into FalkorDB AudioMemo nodes."""
    counter = _empty_counter("audio")
    t0 = time.monotonic()

    gs = config.get_graph_store()
    if gs is None:
        _log.debug("Graph reconcile: graph store unavailable — skipping audio")
        counter["duration_ms"] = int((time.monotonic() - t0) * 1000)
        return counter

    ms = config.get_metadata_store()
    lim = _stale_limit_clause(limit)
    rows = ms.fetch_all(
        f"SELECT audio_id, file_path, duration_seconds, user_id, scope, published_from "  # noqa: S608
        f"FROM audio_memos "
        f"WHERE {_STALE_CONDITION} {lim}"
    )
    counter["scanned"] = len(rows)

    from graph.writer import project_audio

    for row in rows:
        try:
            project_audio(
                gs,
                audio_id=row["audio_id"],
                file_path=row.get("file_path", ""),
                duration_seconds=float(row.get("duration_seconds") or 0.0),
                user_id=row["user_id"],
                scope=row.get("scope", PERSONAL_SCOPE),
                published_from=_pf(row),
                ms=ms,
            )
            counter["projected_ok"] += 1
            counter["stamped"] += 1
        except Exception:
            _log.exception("Graph reconcile: audio projection failed audio_id=%s", row["audio_id"])
            counter["projected_failed"] += 1

    counter["duration_ms"] = int((time.monotonic() - t0) * 1000)
    _log_counter(counter)
    return counter


# ---------------------------------------------------------------------------
# Garbage collection: orphan FalkorDB nodes (ENTITY_MERGED webhook-loss
# recovery — no audit table available, see plan §"Webhook-loss recovery")
# ---------------------------------------------------------------------------


# Labels the orphan GC may touch (LUM-952). Shared/system projections carry
# ``:Entity``; personal entity nodes carry exactly one ENTITY_TYPE_MAP label
# (graph/writer.py::project_entity). NEVER widen this to a label-agnostic match:
# evidence nodes (Document, Session, Note, AudioMemo), code-structure nodes
# (Component, Library) and Memory nodes also carry ``lumogis_id`` + ``user_id``
# and no ``scope``, and none of them has an ``entities`` row.
_GC_ENTITY_LABEL = NodeLabel.SHARED_ENTITY
_GC_LABELS: tuple[str, ...] = (_GC_ENTITY_LABEL, *NodeLabel.ENTITY_LABELS)
_GC_DELETE_BATCH = 200


def _gc_label_predicate(var: str) -> str:
    """``(n:Entity OR n:Person OR …)`` for ``var``, built from constants only."""
    return "(" + " OR ".join(f"{var}:{label}" for label in _GC_LABELS) + ")"


def _gc_label_of(labels) -> str | None:
    for label in labels or ():
        if label in _GC_LABELS:
            return label
    return None


def _gc_guard_trips(personal_orphans: int, personal_scanned: int) -> bool:
    """Mass-delete guard over PERSONAL orphans (LUM-952).

    Refuse above an absolute cap, or above a ratio once a floor is reached so a
    single real merge loser in a tiny graph never trips the ratio rule.
    """
    if personal_orphans > config.get_gc_max_orphans():
        return True
    if personal_orphans < config.get_gc_min_orphans_for_ratio() or personal_scanned == 0:
        return False
    return personal_orphans / personal_scanned > config.get_gc_max_orphan_ratio()


def garbage_collect_orphan_nodes(
    limit: int | None = None, *, dry_run: bool = False, force: bool = False
) -> dict:
    """Delete FalkorDB entity nodes whose ``(user_id, lumogis_id)`` has no PG row.

    Background:
        Core's `services/entity_merge.py:apply_merge` deletes the loser
        `entities` row immediately after a merge. If the corresponding
        `ENTITY_MERGED` webhook was lost (KG offline), the loser FalkorDB
        node remains, no longer pointing at any PG row. Unshare and purge rely
        on this pass too: they delete the shared Postgres row and leave the
        shared `:Entity` node to it (`document_entity_cascade.py`).

    Scope (LUM-952):
        Nodes labelled `:Entity` (shared/system projections) or any
        `ENTITY_TYPE_MAP` label (personal entity nodes: `:Person`,
        `:Organisation`, `:Project`, `:Concept`). Nothing else — see
        `_GC_LABELS`.

    Strategy:
        1. Scan FalkorDB FIRST, then read the live `entities` set. An entity
           committed and projected during the pass is then in the PG read and
           is kept; the old PG-first order deleted it as an orphan, and its
           stamped `graph_projected_at` meant it was never re-projected.
        2. Guard: unless `force`, refuse to delete PERSONAL orphans when there
           are more than `GRAPH_GC_MAX_ORPHANS`, or when at least
           `GRAPH_GC_MIN_ORPHANS_FOR_RATIO` of them exceed
           `GRAPH_GC_MAX_ORPHAN_RATIO` of the personal nodes scanned. A wrong or
           truncated live set must not wipe the user graph. `:Entity` orphans
           are deleted as before, so shared-projection teardown is never held.
        3. `dry_run` counts without deleting.
        4. Delete by internal id in batches, re-checking user, id and label so
           a reused id can never take a different node.

    Arguments:
        limit:   optional cap on orphans deleted per run (None = no cap).
        dry_run: count only.
        force:   ignore the guard.

    Returns:
        Dict: `scanned`, `orphans_found`, `orphans_by_label`, `deleted_ok`,
        `deleted_failed`, `refused`, `dry_run`, `duration_ms`.
    """
    counter: dict = {
        "scanned": 0,
        "orphans_found": 0,
        "orphans_by_label": {},
        "deleted_ok": 0,
        "deleted_failed": 0,
        "refused": False,
        "dry_run": bool(dry_run),
        "duration_ms": 0,
    }
    t0 = time.monotonic()

    def _done() -> dict:
        counter["duration_ms"] = int((time.monotonic() - t0) * 1000)
        return counter

    gs = config.get_graph_store()
    if gs is None:
        _log.debug("Graph reconcile: graph store unavailable — skipping orphan GC")
        return _done()

    try:
        falkor_rows = gs.query(
            f"MATCH (e) WHERE {_gc_label_predicate('e')} "
            "RETURN id(e) AS nid, e.user_id AS uid, e.lumogis_id AS lid, labels(e) AS labels",
            {},
        )
    except Exception:
        _log.exception("Graph reconcile: orphan-node MATCH failed — aborting pass")
        return _done()

    ms = config.get_metadata_store()
    try:
        rows = ms.fetch_all("SELECT entity_id, user_id FROM entities")
    except Exception:
        _log.exception("Graph reconcile: failed to load PG entities for orphan GC — aborting pass")
        return _done()

    live_ids: set[tuple[str, str]] = {(str(r["user_id"]), str(r["entity_id"])) for r in rows}
    counter["scanned"] = len(falkor_rows)

    from graph.writer import (
        on_entity_merged_unknown_loser,  # noqa: F401  (kept for future outbox replay; see writer docstring)
    )

    shared_orphans: list[dict] = []
    personal_orphans: list[dict] = []
    personal_scanned = 0
    for row in falkor_rows:
        uid, lid = row.get("uid"), row.get("lid")
        label = _gc_label_of(row.get("labels"))
        if label is None:
            continue
        is_personal = label != _GC_ENTITY_LABEL
        personal_scanned += int(is_personal)
        if uid is None or lid is None or (str(uid), str(lid)) in live_ids:
            continue
        counter["orphans_found"] += 1
        counter["orphans_by_label"][label] = counter["orphans_by_label"].get(label, 0) + 1
        orphan = {"nid": row.get("nid"), "uid": uid, "lid": lid}
        (personal_orphans if is_personal else shared_orphans).append(orphan)

    if not force and not live_ids and counter["scanned"] > 0:
        # An empty live set against a non-empty graph is the "wrong database /
        # truncated read" failure the guard exists for. Shared nodes are exempt
        # from the ratio guard (unshare/purge teardown), but not from this.
        counter["refused"] = True
        _log.warning(
            "Graph reconcile: orphan GC refused: the entities read returned no rows while "
            "the graph holds %d entity nodes (force via POST /kg/orphan-gc after a dry run)",
            counter["scanned"],
        )
        shared_orphans, personal_orphans = [], []
    elif (
        personal_orphans and not force and _gc_guard_trips(len(personal_orphans), personal_scanned)
    ):
        counter["refused"] = True
        _log.warning(
            "Graph reconcile: orphan GC refused for personal nodes: personal_orphans=%d "
            "personal_scanned=%d (tune GRAPH_GC_MAX_ORPHANS / GRAPH_GC_MAX_ORPHAN_RATIO / "
            "GRAPH_GC_MIN_ORPHANS_FOR_RATIO, or run POST /kg/orphan-gc with force after a dry run)",
            len(personal_orphans),
            personal_scanned,
        )
        personal_orphans = []

    if dry_run:
        return _done()

    to_delete = shared_orphans + personal_orphans
    if limit is not None:
        to_delete = to_delete[:limit]
    delete_cypher = (
        "UNWIND $rows AS r "
        f"MATCH (n) WHERE id(n) = r.nid AND n.user_id = r.uid AND n.lumogis_id = r.lid "
        f"AND {_gc_label_predicate('n')} "
        "WITH n, n.lumogis_id AS lid DETACH DELETE n RETURN count(lid) AS deleted"
    )
    for start in range(0, len(to_delete), _GC_DELETE_BATCH):
        batch = to_delete[start : start + _GC_DELETE_BATCH]
        try:
            res = gs.query(delete_cypher, {"rows": batch})
            # Count what FalkorDB actually deleted: a node that vanished or whose
            # id was reused since the scan fails the re-check and is not counted.
            deleted = int(res[0]["deleted"]) if res and "deleted" in res[0] else 0
            counter["deleted_ok"] += deleted
            counter["deleted_failed"] += len(batch) - deleted
        except Exception:
            _log.exception("Graph reconcile: orphan delete batch failed (rows=%d)", len(batch))
            counter["deleted_failed"] += len(batch)

    _log.info(
        "component=graph_reconcile projection_type=orphan_gc scanned=%d orphans_found=%d "
        "deleted_ok=%d deleted_failed=%d refused=%s duration_ms=%d",
        counter["scanned"],
        counter["orphans_found"],
        counter["deleted_ok"],
        counter["deleted_failed"],
        counter["refused"],
        int((time.monotonic() - t0) * 1000),
    )
    return _done()


def _is_uuid(value: object) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


_RECOUNT_CHUNK = 500
# Personal = scope absent (today's writer) or 'personal' (LUM-961 stamps it).
_PERSONAL_SCOPE = (
    "coalesce(a.scope, 'personal') = 'personal' AND coalesce(b.scope, 'personal') = 'personal'"
)


def recount_cooccurrence(user_id: str | None = None, *, dry_run: bool = False) -> dict:
    """One-off recount of personal RELATES_TO co_occurrence_count (LUM-952).

    The projection write is monotone (it never lowers a count), so this is the
    only path that lowers an inflated edge. Only edges that already carry a
    count are touched: typed RELATES_TO edges (LUM-580) share the pair slot and
    have none. A computed 0 is never written (`zero_skipped`). Personal nodes
    are matched as "scope absent or 'personal'": today's writer leaves it unset,
    and LUM-961 stamps 'personal' (draft ADR); shared/system edges are never
    touched.
    """
    out = {"edges_scanned": 0, "edges_changed": 0, "skipped": 0, "zero_skipped": 0, "users": 0}
    gs = config.get_graph_store()
    if gs is None:
        return out
    ms = config.get_metadata_store()
    personal = [label for label in _GC_LABELS if label != _GC_ENTITY_LABEL]
    a_pred = "(" + " OR ".join(f"a:{label}" for label in personal) + ")"
    b_pred = "(" + " OR ".join(f"b:{label}" for label in personal) + ")"

    if user_id is None:
        try:
            users = [
                r["uid"]
                for r in gs.query(
                    f"MATCH (a)-[r:RELATES_TO]->(b) WHERE {a_pred} AND {b_pred} "
                    "AND r.co_occurrence_count IS NOT NULL RETURN DISTINCT a.user_id AS uid",
                    {},
                )
                if r.get("uid") is not None
            ]
        except Exception:
            _log.exception("Graph reconcile: recount user scan failed")
            return out
    else:
        users = [user_id]

    for uid in users:
        out["users"] += 1
        try:
            edges = gs.query(
                f"MATCH (a)-[r:RELATES_TO]->(b) WHERE {a_pred} AND {b_pred} "
                "AND a.user_id = $uid AND b.user_id = $uid "
                f"AND {_PERSONAL_SCOPE} "
                "AND r.co_occurrence_count IS NOT NULL "
                "RETURN a.lumogis_id AS x, b.lumogis_id AS y, r.co_occurrence_count AS c",
                {"uid": uid},
            )
        except Exception:
            _log.exception("Graph reconcile: recount edge scan failed for user_id=%s", uid)
            continue
        valid = []
        for e in edges:
            out["edges_scanned"] += 1
            if _is_uuid(e.get("x")) and _is_uuid(e.get("y")):
                valid.append(e)
            else:
                out["skipped"] += 1
        for start in range(0, len(valid), _RECOUNT_CHUNK):
            chunk = valid[start : start + _RECOUNT_CHUNK]
            try:
                counted = ms.fetch_all(
                    "SELECT p.x::text AS x, p.y::text AS y, count(DISTINCT a.evidence_id) AS n "
                    "FROM unnest(%s::uuid[], %s::uuid[]) AS p(x, y) "
                    "JOIN entity_relations a ON a.source_id = p.x AND a.user_id = %s "
                    "JOIN entity_relations b ON b.source_id = p.y AND b.user_id = %s "
                    "  AND b.evidence_id = a.evidence_id "
                    "GROUP BY p.x, p.y",
                    ([str(e["x"]) for e in chunk], [str(e["y"]) for e in chunk], uid, uid),
                )
            except Exception:
                _log.exception("Graph reconcile: recount aggregate failed for user_id=%s", uid)
                continue
            n_by_pair = {(str(r["x"]), str(r["y"])): int(r["n"]) for r in counted}
            for e in chunk:
                n = n_by_pair.get((str(e["x"]), str(e["y"])), 0)
                if n == 0:
                    out["zero_skipped"] += 1
                    continue
                if int(e.get("c") or 0) == n:
                    continue
                if not dry_run:
                    try:
                        gs.query(
                            "MATCH (a)-[r:RELATES_TO]->(b) "
                            "WHERE a.lumogis_id = $x AND a.user_id = $uid "
                            "AND b.lumogis_id = $y AND b.user_id = $uid "
                            f"AND {a_pred} AND {b_pred} AND {_PERSONAL_SCOPE} "
                            "AND r.co_occurrence_count IS NOT NULL "
                            "SET r.co_occurrence_count = $n",
                            {"x": str(e["x"]), "y": str(e["y"]), "uid": uid, "n": n},
                        )
                    except Exception:
                        _log.exception("Graph reconcile: recount write failed for user_id=%s", uid)
                        continue
                out["edges_changed"] += 1
    _log.info(
        "component=graph_reconcile projection_type=cooccurrence_recount users=%d edges_scanned=%d "
        "edges_changed=%d skipped=%d zero_skipped=%d dry_run=%s",
        out["users"],
        out["edges_scanned"],
        out["edges_changed"],
        out["skipped"],
        out["zero_skipped"],
        dry_run,
    )
    return out


# ---------------------------------------------------------------------------
# Temporal backfill (LUM-104) — idempotent, LLM-free, app-side normalised
# ---------------------------------------------------------------------------


def _normalised_backfill_valid_at(last_seen: object, ts: object) -> str:
    """Compute the backfill valid_at value in the fixed graph format.

    Sources, in order: RELATES_TO ``last_seen_at`` (legacy bare-isoformat
    values from ``_now_iso()`` included), MENTIONS/DISCUSSED_IN ``timestamp``,
    else now. ALWAYS normalised app-side through parse_graph_ts/to_graph_ts —
    a Cypher-side ``coalesce`` copy would seed a mixed-format corpus that
    silently breaks lexicographic ordering (the ``Z`` vs ``.`` byte).
    """
    for candidate in (last_seen, ts):
        if candidate:
            try:
                return to_graph_ts(parse_graph_ts(str(candidate)))
            except (ValueError, TypeError):
                _log.warning("temporal backfill: unparseable timestamp — using now")
    return to_graph_ts(now_utc())


def backfill_temporal_properties(gs=None, batch_size: int = 500) -> dict:
    """Backfill LUM-104 temporal properties onto pre-existing graph state.

    Two passes, both idempotent (re-runs match nothing):

    1. **Edges** — for each type in TEMPORAL_EDGE_TYPES, batch-read edges
       missing ``valid_at``, normalise a proxy value app-side (honest but
       coarse: last-seen time), and stamp ``valid_at`` / ``created_at`` /
       ``edge_uid`` / ``contradiction_count`` per edge. ``edge_uid`` is a
       fresh app-side uuid4 per matched edge, never generated in Cypher.
       ``last_seen_at`` itself is left untouched (not a temporal property).
    2. **Evidence nodes** — stamp ``temporal_checked_at`` +
       ``temporal_check_status='backfill'`` on every node missing the stamp,
       so the hourly sweep never LLM-churns the historical corpus.

    A batch that yields no previously-unseen edge ids terminates the loop
    (write failures cannot spin the pass forever). Returns a counter dict.
    """
    counter = {
        "edges_scanned": 0,
        "edges_stamped": 0,
        "edges_failed": 0,
        "evidence_stamped": 0,
        "duration_ms": 0,
    }
    t0 = time.monotonic()

    gs = gs if gs is not None else config.get_graph_store()
    if gs is None:
        _log.debug("temporal backfill: graph store unavailable — skipping")
        counter["duration_ms"] = int((time.monotonic() - t0) * 1000)
        return counter

    for edge_type in TEMPORAL_EDGE_TYPES:
        seen_rids: set = set()
        while True:
            try:
                rows = gs.query(
                    f"MATCH ()-[r:{edge_type}]->() WHERE r.valid_at IS NULL "
                    "RETURN id(r) AS rid, r.last_seen_at AS last_seen, "
                    "r.timestamp AS ts "
                    f"LIMIT {int(batch_size)}",
                    {},
                )
            except Exception:
                _log.exception("temporal backfill: read failed type=%s", edge_type)
                break
            fresh = [r for r in (rows or []) if r.get("rid") not in seen_rids]
            if not fresh:
                break
            for row in fresh:
                rid = row.get("rid")
                seen_rids.add(rid)
                counter["edges_scanned"] += 1
                v = _normalised_backfill_valid_at(row.get("last_seen"), row.get("ts"))
                try:
                    # coalesce on valid_at too (verify-plan fix): a live
                    # min-rule stamp can land between this pass's read and
                    # write — the backfill proxy must fill-if-absent, never
                    # overwrite an extracted value.
                    gs.query(
                        "MATCH ()-[r]->() WHERE id(r) = $rid "
                        "SET r.valid_at = coalesce(r.valid_at, $v), "
                        "r.created_at = coalesce(r.created_at, $v), "
                        "r.edge_uid = coalesce(r.edge_uid, $uid), "
                        "r.contradiction_count = coalesce(r.contradiction_count, 0)",
                        {"rid": rid, "v": v, "uid": uuid.uuid4().hex},
                    )
                    counter["edges_stamped"] += 1
                except Exception:
                    _log.exception("temporal backfill: write failed type=%s rid=%s", edge_type, rid)
                    counter["edges_failed"] += 1
            if len(rows or []) < batch_size:
                break

    now_ts = to_graph_ts(now_utc())
    # Recency cutoff (verify-plan fix): "unstamped" is ALSO the pipeline's
    # sweep-retry marker — a transiently failed recent episode is deliberately
    # left unstamped so the hourly sweep re-drives it. Only nodes older than
    # the cutoff are backfill-stamped, so the nightly reconcile cannot convert
    # pending retries into permanently skipped episodes. Nodes with no
    # timestamp at all ('' sorts before any cutoff) are historical stubs and
    # are stamped.
    cutoff = to_graph_ts(now_utc() - timedelta(hours=24))
    for label in set(EVIDENCE_TYPE_LABEL_MAP.values()):
        while True:
            try:
                rows = gs.query(
                    f"MATCH (n:{label}) WHERE n.temporal_checked_at IS NULL "
                    "AND coalesce(n.created_at, n.ingested_at, n.transcribed_at, '') "
                    "< $cutoff "
                    f"WITH n LIMIT {int(batch_size)} "
                    "SET n.temporal_checked_at = $now_ts, "
                    "n.temporal_check_status = $status "
                    "RETURN count(n) AS cnt",
                    {"now_ts": now_ts, "status": STATUS_BACKFILL, "cutoff": cutoff},
                )
            except Exception:
                _log.exception("temporal backfill: evidence stamp failed label=%s", label)
                break
            cnt = int((rows or [{}])[0].get("cnt") or 0)
            counter["evidence_stamped"] += cnt
            if cnt < batch_size:
                break

    counter["duration_ms"] = int((time.monotonic() - t0) * 1000)
    _log.info(
        "component=graph_reconcile projection_type=temporal_backfill edges_scanned=%d "
        "edges_stamped=%d edges_failed=%d evidence_stamped=%d duration_ms=%d",
        counter["edges_scanned"],
        counter["edges_stamped"],
        counter["edges_failed"],
        counter["evidence_stamped"],
        counter["duration_ms"],
    )
    return counter


# ---------------------------------------------------------------------------
# Typed entity_edges replay (LUM-580)
# ---------------------------------------------------------------------------

# Relation labels are interpolated into Cypher (FalkorDB cannot bind a
# relationship type as a parameter). Values were validated against
# RELATION_TYPES at write time; this shape guard is defence-in-depth for the
# interpolation and is self-contained (the graph tree does not import the
# orchestrator's RELATION_TYPES allowlist).
_REL_TYPE_RE = re.compile(r"[A-Z_]+")

# LUM-599: how many replays that did not attach an edge gets before the pass
# stops scanning it. The reconcile runs daily (03:00), so the default is about a
# week of misses. The async node race the replay exists for resolves within
# seconds, and reconcile_entities (which runs first in the same pass) repairs a
# failed node MERGE, so an edge still unattached after a week points at a node
# that is not coming. Paths that give an edge a new chance reset the counter
# (see migration 080), and an operator can retry everything with
# ``UPDATE entity_edges SET reconcile_attempts = 0 WHERE graph_projected_at IS NULL``.
EDGE_MAX_ATTEMPTS_ENV = "GRAPH_RECONCILE_EDGE_MAX_ATTEMPTS"
DEFAULT_EDGE_MAX_ATTEMPTS = 7


def edge_max_attempts() -> int:
    """Read the LUM-599 attempt cap; a bad or < 1 value falls back to the default.

    Read per call (not at import) so an operator change applies on the next
    pass, and so tests can set it with ``monkeypatch.setenv``.
    """
    raw = os.environ.get(EDGE_MAX_ATTEMPTS_ENV, "").strip()
    if not raw:
        return DEFAULT_EDGE_MAX_ATTEMPTS
    try:
        value = int(raw)
    except ValueError:
        _log.warning(
            "Graph reconcile: %s=%r is not an integer — using %d",
            EDGE_MAX_ATTEMPTS_ENV,
            raw,
            DEFAULT_EDGE_MAX_ATTEMPTS,
        )
        return DEFAULT_EDGE_MAX_ATTEMPTS
    if value < 1:
        _log.warning(
            "Graph reconcile: %s=%d is below 1 — using %d",
            EDGE_MAX_ATTEMPTS_ENV,
            value,
            DEFAULT_EDGE_MAX_ATTEMPTS,
        )
        return DEFAULT_EDGE_MAX_ATTEMPTS
    return value


# The scan and the aged-out count share these clauses, and they match the
# predicate of idx_entity_edges_reconcile_stale (migration 080) word for word so
# the planner can use that partial index. Change all three together.
_EDGE_STALE_WHERE = (
    "graph_projected_at IS NULL AND valid_until IS NULL AND relation_type <> 'RELATES_TO' "
)


def reconcile_entity_edges(limit: int | None = None) -> dict:
    """Replay active, stale, non-``RELATES_TO`` typed ``entity_edges`` (LUM-580).

    A typed edge projects synchronously (``services/entity_edges.py::_project_edge``)
    but the entity NODE it attaches to is MERGE'd asynchronously
    (``fire_background``). On a first-mention entity the edge fires before the
    node exists, so the MATCH-only ``MERGE`` no-ops and the edge is dropped. This
    pass replays every edge the live path did not stamp.

    Correctness invariants:
      * **Stale = ``graph_projected_at IS NULL`` only.** ``entity_edges`` has no
        ``updated_at`` and a projected typed edge is immutable, so there is no
        "changed since projection" case (unlike the entities/file_index passes).
      * **Excludes ``RELATES_TO``.** FalkorDB ``MERGE (a)-[:RELATES_TO]->(b)``
        matches the single co-occurrence aggregate slot (owned by
        ``reconcile_entities`` / ``_update_cooccurrence_edges``); this pass never
        MERGEs, SETs, or reads that slot.
      * **Bank-scoped.** ``entity_edges`` carries a ``bank``; each edge projects
        onto ``get_graph_store(bank)`` — a ``coding`` edge MATCHes nothing on the
        default ``personal`` graph.
      * **Stamp only on attach.** The MATCH-only ``MERGE`` never raises when a
        node is absent, so attachment is read back via ``count(r)``. A row that
        did not attach is left unstamped (``skipped``) and retried next pass —
        never stamp a projection that did not attach.
      * **Bounded retries (LUM-599).** A row that did not attach has its
        ``reconcile_attempts`` incremented. Rows at :func:`edge_max_attempts`
        or above are left out of the scan and reported as ``aged_out``. The
        scan orders by ``reconcile_attempts`` so fresh misses come first when
        ``limit`` truncates it. A confirmed attach resets the counter to 0. Only
        a real miss counts: a FalkorDB error, an unresolvable bank or a disabled
        graph leaves the counter alone.
    """
    counter = _empty_counter("entity_edge")
    counter["skipped"] = 0  # node not yet present (async race) -> retried next pass
    counter["aged_out"] = 0  # LUM-599: retry budget spent -> no longer scanned
    t0 = time.monotonic()

    ms = config.get_metadata_store()
    max_attempts = edge_max_attempts()
    lim = _stale_limit_clause(limit)
    stale = ms.fetch_all(
        "SELECT id, user_id, bank, src_entity_id, dst_entity_id, relation_type "  # noqa: S608
        "FROM entity_edges "
        f"WHERE {_EDGE_STALE_WHERE}"
        "AND reconcile_attempts < %s "
        "ORDER BY reconcile_attempts, id "
        f"{lim}",
        (max_attempts,),
    )
    counter["scanned"] = len(stale)
    try:
        aged = ms.fetch_one(
            "SELECT count(*) AS n FROM entity_edges "  # noqa: S608
            f"WHERE {_EDGE_STALE_WHERE}"
            "AND reconcile_attempts >= %s",
            (max_attempts,),
        )
        counter["aged_out"] = int((aged or {}).get("n") or 0)
    except Exception:
        # Diagnostic only — never fail the pass over the count.
        _log.warning("Graph reconcile: entity_edge aged-out count failed", exc_info=True)

    gs_by_bank: dict = {}
    for row in stale:
        bank = str(row.get("bank") or "personal")
        if bank not in gs_by_bank:
            # get_graph_store validates the bank and RAISES on an unknown value.
            # Reconcile reads historical rows (predating write-time validation,
            # data migrations, direct writes), so a single malformed bank must
            # not abort the whole reconciliation run — skip the row and continue.
            try:
                gs_by_bank[bank] = config.get_graph_store(bank)
            except Exception:
                _log.warning("Graph reconcile: entity_edge unresolvable bank=%s — skipping", bank)
                gs_by_bank[bank] = None
        gs = gs_by_bank[bank]
        if gs is None:
            counter["skipped"] += 1
            continue

        rel = str(row.get("relation_type") or "").strip().upper()
        if not rel or rel == "RELATES_TO" or not _REL_TYPE_RE.fullmatch(rel):
            # RELATES_TO is excluded by the scan too; this is belt-and-braces so
            # the co-occurrence slot can never be touched even on a bad row.
            counter["skipped"] += 1
            continue

        try:
            rows = gs.query(
                # Shape-identical to services/entity_edges.py::_project_edge (the
                # canonical live projection), plus RETURN count(r) to read attach.
                "MATCH (a {lumogis_id: $src, user_id: $user_id}), "
                "(b {lumogis_id: $dst, user_id: $user_id}) "
                f"MERGE (a)-[r:{rel}]->(b) "
                "SET r.user_id = $user_id "
                "RETURN count(r) AS attached",
                {
                    "src": row["src_entity_id"],
                    "dst": row["dst_entity_id"],
                    "user_id": row["user_id"],
                },
            )
        except Exception:
            _log.warning(
                "Graph reconcile: entity_edge projection failed id=%s rel=%s",
                row.get("id"),
                rel,
            )
            counter["projected_failed"] += 1
            continue

        # Defensive parse (symmetric with _project_edge): an unexpected result
        # shape degrades to "not attached", never crashes the pass.
        attached = (
            isinstance(rows, (list, tuple))
            and bool(rows)
            and isinstance(rows[0], dict)
            and bool(rows[0].get("attached"))
        )
        if not attached:
            counter["skipped"] += 1
            # LUM-599: a real miss (the query ran, a node is absent) spends one
            # attempt. Best-effort: a failed increment only means one more retry.
            try:
                ms.execute(
                    "UPDATE entity_edges SET reconcile_attempts = reconcile_attempts + 1 "
                    "WHERE id = %s AND graph_projected_at IS NULL",
                    (row["id"],),
                )
            except Exception:
                _log.warning(
                    "Graph reconcile: entity_edge attempt increment failed id=%s", row.get("id")
                )
            continue

        counter["projected_ok"] += 1
        try:
            ms.execute(
                "UPDATE entity_edges SET graph_projected_at = now(), reconcile_attempts = 0 "
                "WHERE id = %s",
                (row["id"],),
            )
            counter["stamped"] += 1
        except Exception:
            _log.warning("Graph reconcile: entity_edge stamp failed id=%s", row.get("id"))

    counter["duration_ms"] = int((time.monotonic() - t0) * 1000)
    _log_counter(counter)
    # `skipped` (edges still waiting on an async node MERGE, or unresolvable
    # rows) and `aged_out` (LUM-599: retry budget spent, no longer scanned) are
    # the key diagnostics for this pass and are not in the shared _log_counter
    # format — surface them on their own line.
    _log.info(
        "component=graph_reconcile projection_type=entity_edge skipped=%d aged_out=%d "
        "max_attempts=%d",
        counter["skipped"],
        counter["aged_out"],
        max_attempts,
    )
    return counter


# ---------------------------------------------------------------------------
# Combined entry point (called by APScheduler and backfill endpoint)
# ---------------------------------------------------------------------------


def run_reconciliation(limit_per_type: int | None = None) -> dict:
    """Run all five reconciliation passes and return a combined summary dict.

    Safe to call repeatedly — all graph writes are idempotent.
    If the graph store is unavailable, all passes return with scanned=0
    and no errors are raised (the caller sees a valid result dict).

    Arguments:
        limit_per_type: optional cap on rows processed per table per run.
                        None = process all stale rows (default for scheduler).
                        Set a small value (e.g. 500) for incremental backfill.
    """
    t0 = time.monotonic()
    _log.info(
        "Graph reconciliation: starting (limit_per_type=%s)",
        limit_per_type if limit_per_type is not None else "unlimited",
    )

    results = {
        "documents": reconcile_documents(limit=limit_per_type),
        "entities": reconcile_entities(limit=limit_per_type),
        # LUM-586: shared-scope entity projection backstop. Kept OUT of the
        # summed _proj_keys totals below (own unit, like entity_edges/orphan_gc)
        # so existing total assertions stay stable.
        "shared_entities": reconcile_shared_entities(limit=limit_per_type),
        "sessions": reconcile_sessions(limit=limit_per_type),
        "notes": reconcile_notes(limit=limit_per_type),
        "audio": reconcile_audio(limit=limit_per_type),
        # LUM-580: typed entity_edges replay. Kept OUT of the summed _proj_keys
        # totals below (it carries an extra `skipped` key and a distinct unit),
        # mirroring how orphan_gc reports its own shape.
        "entity_edges": reconcile_entity_edges(limit=limit_per_type),
        "orphan_gc": garbage_collect_orphan_nodes(limit=limit_per_type),
    }

    # LUM-104: temporal-property backfill rides the daily reconcile only when
    # the feature flag is on (idempotent — post-first-run passes match nothing).
    if temporal_feature_enabled():
        results["temporal_backfill"] = backfill_temporal_properties()

    # Per-projection counters use different key names; sum only the
    # projection passes (orphan_gc has its own deleted_* shape).
    _proj_keys = ("documents", "entities", "sessions", "notes", "audio")
    totals = {
        "scanned": sum(results[k]["scanned"] for k in _proj_keys),
        "projected_ok": sum(results[k]["projected_ok"] for k in _proj_keys),
        "projected_failed": sum(results[k]["projected_failed"] for k in _proj_keys),
        "stamped": sum(results[k]["stamped"] for k in _proj_keys),
        "orphans_deleted": results["orphan_gc"]["deleted_ok"],
        # LUM-952: a refused pass must be visible in the summary, not only the log.
        "orphan_gc_refused": bool(results["orphan_gc"].get("refused", False)),
        "duration_ms": int((time.monotonic() - t0) * 1000),
    }
    results["totals"] = totals

    _log.info(
        "component=graph_reconcile projection_type=ALL scanned=%d projected_ok=%d "
        "projected_failed=%d stamped=%d duration_ms=%d",
        totals["scanned"],
        totals["projected_ok"],
        totals["projected_failed"],
        totals["stamped"],
        totals["duration_ms"],
    )

    # Write completion timestamp to kg_settings for the job-status endpoint.
    try:
        ms = config.get_metadata_store()
        now_iso = datetime.now(timezone.utc).isoformat()
        ms.execute(
            "INSERT INTO kg_settings (key, value) VALUES ('_job_last_reconciliation', %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (now_iso,),
        )
    except Exception:
        _log.warning(
            "run_reconciliation: failed to write _job_last_reconciliation timestamp to kg_settings",
            exc_info=True,
        )

    return results
