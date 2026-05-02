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
Because all graph writes are idempotent, multiple replays produce no duplicates.

Log policy
----------
NEVER log raw user text. IDs, counts, and safe previews only.
"""

import logging
import time
from datetime import datetime, timezone

import config

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

_STALE_CONDITION = (
    "graph_projected_at IS NULL OR updated_at > graph_projected_at"
)

# file_index has no updated_at trigger — but ingest.py writes updated_at=NOW()
# on every ingest, so the same condition is correct.
_STALE_CONDITION_FILE_INDEX = _STALE_CONDITION


def _stale_limit_clause(limit: int | None) -> str:
    return f"LIMIT {int(limit)}" if limit is not None else ""


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
    rows = ms.fetch_all(
        f"SELECT file_path, file_type, user_id FROM file_index "  # noqa: S608
        f"WHERE {_STALE_CONDITION_FILE_INDEX} {lim}"
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
    stale = ms.fetch_all(
        f"SELECT entity_id, name, entity_type, user_id "  # noqa: S608
        f"FROM entities WHERE ({_STALE_CONDITION}) AND (is_staged IS NOT TRUE) {lim}"
    )
    counter["scanned"] = len(stale)

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

        if not relations:
            # Entity exists in Postgres but has no relations yet — project with
            # a synthetic stub so the node exists for future edge creation.
            relations = [{"evidence_id": entity_id, "evidence_type": "ENTITY"}]

        entity_ok = True
        for rel in relations:
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
                )
            except Exception:
                _log.exception(
                    "Graph reconcile: entity projection failed entity_id=%s type=%s",
                    entity_id,
                    entity_type,
                )
                entity_ok = False
                break  # stop replaying relations for this entity; leave unstamped

        if entity_ok:
            counter["projected_ok"] += 1
            counter["stamped"] += 1
        else:
            counter["projected_failed"] += 1

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
        f"SELECT session_id, summary, topics, entities, entity_ids, user_id "  # noqa: S608
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
        f"SELECT note_id, user_id FROM notes "  # noqa: S608
        f"WHERE {_STALE_CONDITION} {lim}"
    )
    counter["scanned"] = len(rows)

    from graph.writer import project_note

    for row in rows:
        try:
            project_note(gs, note_id=row["note_id"], user_id=row["user_id"], ms=ms)
            counter["projected_ok"] += 1
            counter["stamped"] += 1
        except Exception:
            _log.exception(
                "Graph reconcile: note projection failed note_id=%s", row["note_id"]
            )
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
        f"SELECT audio_id, file_path, duration_seconds, user_id FROM audio_memos "  # noqa: S608
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
                ms=ms,
            )
            counter["projected_ok"] += 1
            counter["stamped"] += 1
        except Exception:
            _log.exception(
                "Graph reconcile: audio projection failed audio_id=%s", row["audio_id"]
            )
            counter["projected_failed"] += 1

    counter["duration_ms"] = int((time.monotonic() - t0) * 1000)
    _log_counter(counter)
    return counter


# ---------------------------------------------------------------------------
# Garbage collection: orphan FalkorDB nodes (ENTITY_MERGED webhook-loss
# recovery — no audit table available, see plan §"Webhook-loss recovery")
# ---------------------------------------------------------------------------

def garbage_collect_orphan_nodes(limit: int | None = None) -> dict:
    """Delete FalkorDB :Entity nodes whose `lumogis_id` no longer exists in PG.

    Background:
        Core's `services/entity_merge.py:apply_merge` deletes the loser
        `entities` row immediately after a merge. If the corresponding
        `ENTITY_MERGED` webhook was lost (KG offline), the loser FalkorDB
        node remains, no longer pointing at any PG row. The winner side is
        re-projected by `reconcile_entities()` (it has `graph_projected_at
        IS NULL` after `apply_merge`), but no in-process call ever cleans
        up the orphan.

    Strategy:
        Stream the live entity-id set from Postgres into a Python `set`,
        page over FalkorDB :Entity nodes, and DETACH DELETE every node
        whose `(user_id, lumogis_id)` is missing from the PG set. We do
        NOT attempt edge transfer because the winner is unrecoverable
        (no audit table). Any dangling edges that referenced the orphan
        are removed by DETACH DELETE; the winner side is re-projected by
        `reconcile_entities` from the canonical PG state, so the net
        effect after one full reconciliation cycle is graph state ==
        "edges materialised from current PG only".

    Limitations (documented honestly in the plan):
        - Runs daily, NOT every reconciliation tick. For ≤24 h KG outages,
          edges may briefly point at orphan nodes until this pass runs.
        - `graph.query_ego` and `graph.get_context` already filter results
          via PG joins, so orphan nodes are silently invisible to
          end-user queries even before GC runs.

    Arguments:
        limit: optional cap on orphans deleted per run (None = no cap).
               Useful for first-time backfill on a long-stale dataset.

    Returns:
        Dict with keys: `scanned` (FalkorDB nodes inspected), `orphans_found`,
        `deleted_ok`, `deleted_failed`, `duration_ms`.
    """
    counter = {
        "scanned": 0,
        "orphans_found": 0,
        "deleted_ok": 0,
        "deleted_failed": 0,
        "duration_ms": 0,
    }
    t0 = time.monotonic()

    gs = config.get_graph_store()
    if gs is None:
        _log.debug("Graph reconcile: graph store unavailable — skipping orphan GC")
        counter["duration_ms"] = int((time.monotonic() - t0) * 1000)
        return counter

    ms = config.get_metadata_store()

    try:
        rows = ms.fetch_all("SELECT entity_id, user_id FROM entities")
    except Exception:
        _log.exception("Graph reconcile: failed to load PG entities for orphan GC — aborting pass")
        counter["duration_ms"] = int((time.monotonic() - t0) * 1000)
        return counter

    live_ids: set[tuple[str, str]] = {
        (str(r["user_id"]), str(r["entity_id"])) for r in rows
    }

    try:
        falkor_rows = gs.query(
            "MATCH (e:Entity) RETURN e.user_id AS uid, e.lumogis_id AS lid",
            {},
        )
    except Exception:
        _log.exception("Graph reconcile: orphan-node MATCH failed — aborting pass")
        counter["duration_ms"] = int((time.monotonic() - t0) * 1000)
        return counter

    counter["scanned"] = len(falkor_rows)

    from graph.writer import on_entity_merged_unknown_loser  # noqa: F401  (kept for future outbox replay; see writer docstring)

    for row in falkor_rows:
        uid = row.get("uid")
        lid = row.get("lid")
        if uid is None or lid is None:
            continue
        if (str(uid), str(lid)) in live_ids:
            continue
        counter["orphans_found"] += 1
        if limit is not None and counter["deleted_ok"] >= limit:
            continue
        try:
            gs.query(
                "MATCH (n:Entity) WHERE n.user_id = $uid AND n.lumogis_id = $lid "
                "DETACH DELETE n",
                {"uid": uid, "lid": lid},
            )
            counter["deleted_ok"] += 1
        except Exception:
            _log.exception(
                "Graph reconcile: orphan delete failed user_id=%s lumogis_id=%s",
                uid,
                lid,
            )
            counter["deleted_failed"] += 1

    counter["duration_ms"] = int((time.monotonic() - t0) * 1000)
    _log.info(
        "component=graph_reconcile projection_type=orphan_gc scanned=%d orphans_found=%d "
        "deleted_ok=%d deleted_failed=%d duration_ms=%d",
        counter["scanned"],
        counter["orphans_found"],
        counter["deleted_ok"],
        counter["deleted_failed"],
        counter["duration_ms"],
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
        "sessions": reconcile_sessions(limit=limit_per_type),
        "notes": reconcile_notes(limit=limit_per_type),
        "audio": reconcile_audio(limit=limit_per_type),
        "orphan_gc": garbage_collect_orphan_nodes(limit=limit_per_type),
    }

    # Per-projection counters use different key names; sum only the
    # projection passes (orphan_gc has its own deleted_* shape).
    _proj_keys = ("documents", "entities", "sessions", "notes", "audio")
    totals = {
        "scanned": sum(results[k]["scanned"] for k in _proj_keys),
        "projected_ok": sum(results[k]["projected_ok"] for k in _proj_keys),
        "projected_failed": sum(results[k]["projected_failed"] for k in _proj_keys),
        "stamped": sum(results[k]["stamped"] for k in _proj_keys),
        "orphans_deleted": results["orphan_gc"]["deleted_ok"],
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
