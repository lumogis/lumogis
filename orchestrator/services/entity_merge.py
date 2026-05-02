# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Entity merge service — Pass 4a of the KG Quality Pipeline.

Implements two-phase manual entity merging:

Phase A — single Postgres transaction (all-or-nothing)
  1. Verify both entities exist and belong to user_id
  2. UPDATE entity_relations: redirect loser rows to winner
  3. UPDATE sessions: replace loser UUID in entity_ids arrays via array_replace
  4. Merge aliases (union), context_tags (union), mention_count (sum) onto winner
  5. NULL winner.graph_projected_at — triggers FalkorDB re-projection on next cycle
  6. DELETE loser entity
  7. COMMIT
  8. Fire Event.ENTITY_MERGED hook (after commit, outside transaction)

Phase B — best-effort Qdrant cleanup (outside transaction, never rolls back Phase A)
  - Delete Qdrant points matching loser entity_id
  - Re-upsert winner point with merged aliases
  - On failure: log ERROR, set qdrant_cleaned=False — Postgres commit stands

Cascade note
------------
review_queue has a FK ON DELETE CASCADE to entities. Deleting the loser entity
automatically removes its review_queue rows. This is accepted behaviour — the
merge audit trail is preserved in review_decisions (written by the caller).

Transaction implementation
--------------------------
PostgresStore uses autocommit=True. For Phase A we temporarily disable
autocommit on the underlying psycopg2 connection, execute the transaction,
then restore autocommit. This is safe because PostgresStore is a singleton
and we hold the GIL during each statement.
"""

import logging
import uuid as _uuid

import hooks
from events import Event
from models.entities import MergeResult

import config

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run_phase_a(ms, winner_id: str, loser_id: str, user_id: str) -> dict:
    """Execute Phase A inside a single Postgres transaction.

    Returns a dict with relations_moved, sessions_updated, aliases_merged.
    Raises RuntimeError (wrapping the original exception) on any failure.
    The caller is responsible for committing or rolling back via ms._conn.
    """
    conn = ms._conn

    # Step 1: Verify both entities exist and belong to user_id
    winner_row = ms.fetch_one(
        "SELECT entity_id, name, aliases, context_tags, mention_count "
        "FROM entities WHERE entity_id = %s AND user_id = %s",
        (winner_id, user_id),
    )
    if winner_row is None:
        raise ValueError(f"winner entity {winner_id!r} not found for user_id={user_id!r}")

    loser_row = ms.fetch_one(
        "SELECT entity_id, name, aliases, context_tags, mention_count "
        "FROM entities WHERE entity_id = %s AND user_id = %s",
        (loser_id, user_id),
    )
    if loser_row is None:
        raise ValueError(f"loser entity {loser_id!r} not found for user_id={user_id!r}")

    # Step 2: Move entity_relations from loser to winner.
    # Two-pass to respect the post-012 UNIQUE(source_id, evidence_id,
    # relation_type, user_id):
    #   (a) UPDATE only the rows where the winner does NOT already have
    #       the same (evidence_id, relation_type, user_id) triple — these
    #       are unique to the loser and are safely re-pointed.
    #   (b) DELETE the remaining loser rows (the ones whose re-point would
    #       have collided with an existing winner row). The winner's
    #       first-observed created_at is preserved by design.
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE entity_relations SET source_id = %s "
            "WHERE source_id = %s AND user_id = %s "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM entity_relations er2 "
            "    WHERE er2.source_id = %s "
            "      AND er2.evidence_id = entity_relations.evidence_id "
            "      AND er2.relation_type = entity_relations.relation_type "
            "      AND er2.user_id = entity_relations.user_id"
            "  )",
            (winner_id, loser_id, user_id, winner_id),
        )
        relations_moved = cur.rowcount

    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM entity_relations "
            "WHERE source_id = %s AND user_id = %s",
            (loser_id, user_id),
        )
        relations_dropped_as_duplicate = cur.rowcount

    # Step 3: Replace loser UUID in sessions.entity_ids arrays
    # SCOPE-EXEMPT: per-user merge transaction (plan §2.11) — operates on the
    # owning user's session rows only; merges never cross user boundaries.
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE sessions "
            "SET entity_ids = array_replace(entity_ids, %s::uuid, %s::uuid) "
            "WHERE user_id = %s AND %s::uuid = ANY(entity_ids)",
            (loser_id, winner_id, user_id, loser_id),
        )
        sessions_updated = cur.rowcount

    # Step 4: Merge aliases, context_tags, mention_count onto winner
    winner_aliases = list(winner_row.get("aliases") or [])
    loser_aliases  = list(loser_row.get("aliases") or [])
    winner_tags    = list(winner_row.get("context_tags") or [])
    loser_tags     = list(loser_row.get("context_tags") or [])

    merged_aliases = list(dict.fromkeys(winner_aliases + loser_aliases))
    merged_tags    = list(dict.fromkeys(winner_tags + loser_tags))
    merged_count   = (winner_row.get("mention_count") or 0) + (loser_row.get("mention_count") or 0)
    aliases_merged = len(merged_aliases) - len(winner_aliases)

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE entities SET "
            "  aliases = %s, "
            "  context_tags = %s, "
            "  mention_count = %s, "
            "  graph_projected_at = NULL, "
            "  updated_at = NOW() "
            "WHERE entity_id = %s AND user_id = %s",
            (merged_aliases, merged_tags, merged_count, winner_id, user_id),
        )

    # Step 5: Already handled above (graph_projected_at = NULL)

    # Step 5b: Re-point shared/system projections away from the loser
    # BEFORE deleting it.  Required by plan §2.11 rule 31:
    #   "Merge-remap atomicity: if merge_entities(A → B) commits, a
    #    sweep within the same Postgres transaction must update
    #    `published_from` to point to `B` (and remove duplicates
    #    revealed by the partial unique index) across every
    #    projection-capable table.  After commit, no `published_from`
    #    value anywhere references the loser `A`."
    # MUST run before Step 6's DELETE because every projection table
    # has `published_from REFERENCES <table>(<pk>) ON DELETE CASCADE`
    # — without this remap, projection rows would be CASCADE-deleted
    # instead of being re-pointed to the survivor.
    from services.projection import remap_published_from
    remap_published_from(loser_id, winner_id)
    projections_remapped = True

    # Step 6: Delete loser entity (review_queue rows CASCADE)
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM entities WHERE entity_id = %s AND user_id = %s",
            (loser_id, user_id),
        )

    return {
        "relations_moved":  relations_moved,
        "sessions_updated": sessions_updated,
        "aliases_merged":   max(0, aliases_merged),
        "relations_dropped_as_duplicate": relations_dropped_as_duplicate,
        "projections_remapped": projections_remapped,
    }


def _run_phase_b(winner_id: str, loser_id: str, user_id: str, winner_name: str, merged_aliases: list[str]) -> bool:
    """Best-effort Qdrant cleanup.  Returns True on success, False on any failure.

    Never raises — Postgres commit is never rolled back from here.
    """
    try:
        vs = config.get_vector_store()
        embedder = config.get_embedder()

        # Delete loser Qdrant point(s) by entity_id payload filter
        try:
            vs.delete_where(
                collection="entities",
                filter={"must": [{"key": "entity_id", "match": {"value": loser_id}}]},
            )
        except Exception:
            _log.exception(
                "entity_merge: Phase B — failed to delete loser Qdrant point loser_id=%s",
                loser_id,
            )
            return False

        # Re-upsert winner with merged aliases
        try:
            import uuid as _uuid_mod
            embed_text = winner_name
            if merged_aliases:
                embed_text += f": {', '.join(merged_aliases[:5])}"
            vector = embedder.embed(embed_text)
            point_id = str(_uuid_mod.uuid5(_uuid_mod.NAMESPACE_URL, f"entity::{user_id}::{winner_name.lower()}"))
            vs.upsert(
                collection="entities",
                id=point_id,
                vector=vector,
                payload={
                    "entity_id": winner_id,
                    "name": winner_name,
                    "user_id": user_id,
                    "aliases": merged_aliases,
                },
            )
        except Exception:
            _log.exception(
                "entity_merge: Phase B — failed to re-upsert winner Qdrant point winner_id=%s",
                winner_id,
            )
            return False

        return True
    except Exception:
        _log.exception(
            "entity_merge: Phase B — unexpected error winner_id=%s loser_id=%s",
            winner_id,
            loser_id,
        )
        return False


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def merge_entities(
    winner_id: str,
    loser_id: str,
    user_id: str,
) -> MergeResult:
    """Merge loser entity into winner in two phases.

    Phase A (Postgres transaction): moves relations, updates sessions, merges
    metadata, nulls graph_projected_at, deletes loser.  Fires ENTITY_MERGED hook
    after commit.  Raises RuntimeError on any SQL failure (no Phase B, no hook).

    Phase B (Qdrant, best-effort): deletes loser point, re-upserts winner.
    Failure sets qdrant_cleaned=False but does not reverse Phase A.

    Raises:
        ValueError: if winner_id == loser_id, or if either entity is not found
                    for the given user_id.
        RuntimeError: on SQL error during Phase A (transaction rolled back).
    """
    if winner_id == loser_id:
        raise ValueError(f"winner_id and loser_id must differ (got {winner_id!r})")

    ms = config.get_metadata_store()
    conn = ms._conn  # direct psycopg2 connection

    # Phase A — single transaction
    # PostgresStore uses autocommit=True; disable it for this block.
    try:
        conn.autocommit = False
    except Exception as exc:
        raise RuntimeError(f"entity_merge: failed to begin transaction: {exc}") from exc

    phase_a: dict = {}
    winner_row_cached: dict = {}
    try:
        # Pre-fetch winner name before any mutations (needed for Phase B)
        winner_pre = ms.fetch_one(
            "SELECT name, aliases FROM entities WHERE entity_id = %s AND user_id = %s",
            (winner_id, user_id),
        )

        phase_a = _run_phase_a(ms, winner_id, loser_id, user_id)

        # Fetch final merged aliases for Phase B (post-mutation state)
        winner_post = ms.fetch_one(
            "SELECT name, aliases FROM entities WHERE entity_id = %s AND user_id = %s",
            (winner_id, user_id),
        )

        conn.commit()
        _log.info(
            "entity_merge: Phase A committed winner=%s loser=%s relations_moved=%d "
            "sessions_updated=%d aliases_merged=%d relations_dropped_as_duplicate=%d",
            winner_id,
            loser_id,
            phase_a["relations_moved"],
            phase_a["sessions_updated"],
            phase_a["aliases_merged"],
            phase_a.get("relations_dropped_as_duplicate", 0),
        )
        winner_row_cached = winner_post or winner_pre or {}
    except ValueError:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise RuntimeError(
            f"entity_merge: Phase A failed for winner={winner_id} loser={loser_id}: {exc}"
        ) from exc
    finally:
        conn.autocommit = True

    # Fire ENTITY_MERGED hook (after commit, outside transaction)
    hooks.fire_background(
        Event.ENTITY_MERGED,
        winner_id=winner_id,
        loser_id=loser_id,
        user_id=user_id,
    )

    # Phase B — best-effort Qdrant cleanup
    winner_name    = winner_row_cached.get("name", "")
    merged_aliases = list(winner_row_cached.get("aliases") or [])
    qdrant_cleaned = _run_phase_b(winner_id, loser_id, user_id, winner_name, merged_aliases)
    if not qdrant_cleaned:
        _log.error(
            "entity_merge: Phase B failed — Qdrant not fully synced winner=%s loser=%s "
            "(Postgres commit stands)",
            winner_id,
            loser_id,
        )

    return MergeResult(
        winner_id=winner_id,
        loser_id=loser_id,
        aliases_merged=phase_a["aliases_merged"],
        relations_moved=phase_a["relations_moved"],
        sessions_updated=phase_a["sessions_updated"],
        qdrant_cleaned=qdrant_cleaned,
    )
