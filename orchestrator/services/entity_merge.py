# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Entity merge service — Pass 4a of the KG Quality Pipeline.

Implements two-phase manual entity merging:

Phase A — single Postgres transaction (all-or-nothing)
  1. Verify both entities exist and belong to user_id
  2. UPDATE entity_relations: redirect loser rows to winner
  3. UPDATE sessions: replace loser UUID in entity_ids arrays via array_replace
  4. Merge aliases (union), context_tags (union), mention_count (sum) onto winner
  4b. Repoint active entity_edges loser -> winner (LUM-598)
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
Phase A runs inside ``ms.transaction()`` (LUM-816), which checks a connection
out of the pool and binds it to this thread for the block.

It used to reach for ``ms._conn`` and toggle ``autocommit`` by hand, justified
in this docstring as *"safe because PostgresStore is a singleton and we hold the
GIL during each statement"*. **That was wrong.** The GIL is released during the
I/O every statement blocks on, so another thread runs precisely *between*
statements — which is when the shared ``autocommit`` flag was observed. It was
the same hazard as the old ``transaction()``, minus the guard that would at
least have raised.
"""

import logging

import hooks
from events import Event
from models.entities import MergeResult

import config

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _affected(ms, counting_sql: str, params: tuple) -> int:
    """Read the row count from a ``WITH affected AS (...) SELECT count(*)`` query.

    ``cur.rowcount`` gave this for free, but reaching it meant driving a raw
    cursor on ``ms._conn`` — the process-wide connection this whole ticket
    exists to stop sharing. The first replacement was
    ``len(fetch_all(... RETURNING 1))``, which is correct but ships every
    affected row to the client and builds a dict per row purely to count them:
    on a merge touching 50k relations that is 50k round-trip rows and 50k dicts,
    inside an open transaction holding their locks the whole time.

    Wrapping the DML in a CTE lets Postgres do the counting it was already
    doing. Each caller passes its **complete** statement — this helper composes
    no SQL, because the repo's rule against building query text with f-strings
    does not have a "but the fragment is a module constant" exception, and a
    helper that takes a fragment is one careless caller away from taking a
    variable.
    """
    row = ms.fetch_one(counting_sql, params)
    return int((row or {}).get("n") or 0)


def _repoint_entity_edges(ms, winner_id: str, loser_id: str, user_id: str) -> dict:
    """Repoint active ``entity_edges`` endpoints from ``loser_id`` to ``winner_id``.

    Runs inside the merge transaction (LUM-598). Four ordered statements, each a
    complete parameterised statement (see :func:`_affected`):

    1. **Self-loops are archived, not repointed.** After the merge a loser<->winner
       edge (either direction) or an existing loser->loser edge would relate the
       survivor to itself. The repo treats a self-loop as a defect (the
       ``no_self_loop`` CRITICAL check in ``entity_constraints``), so the edge is
       retired with ``valid_until = now()`` — the same soft-archive that ``forget``
       uses (ADR 129; "never destroys", ADR 151) — rather than hard-deleted.
    2. **Duplicates are dropped.** An active loser edge whose repointed
       ``(user_id, bank, src, dst, relation_type)`` already exists as an ACTIVE
       winner edge is redundant (the UNIQUE index would reject it), so it is
       deleted and the winner's edge — its ``valid_from`` / ``created_at`` /
       ``evidence_id`` — wins, exactly as ``entity_relations`` dedup does in
       Step 2.
    3. **The rest are repointed** with ``graph_projected_at = NULL`` so the LUM-580
       reconcile pass (stale = ``graph_projected_at IS NULL``) attaches them to
       the winner's node, and ``reconcile_attempts = 0`` so an edge that aged out
       against the loser's missing node is retried (LUM-599). ``id``,
       ``evidence_id``, ``valid_from``, ``valid_until`` and ``created_at`` are
       deliberately NOT in the SET list: the temporal
       window of a fact does not change because its subject was merged.
    4. **Stragglers are counted, not guessed.** An active loser edge that collides
       with an ARCHIVED winner edge (same key; archived rows still occupy the
       UNIQUE slot) is left untouched. Reviving the archived winner edge or
       dropping the live loser one is a temporal-validity decision (ADR 133/151
       R1), not a merge mechanic; it is surfaced via ``edges_unmerged`` and a
       warning instead.
    """
    selfloops_archived = _affected(
        ms,
        # SCOPE-EXEMPT: per-user merge transaction (plan §2.11); every row is user_id-bound.
        "WITH affected AS ("
        "  UPDATE entity_edges SET valid_until = now() "
        "  WHERE user_id = %s AND valid_until IS NULL "
        "    AND ((src_entity_id = %s AND dst_entity_id IN (%s, %s)) "
        "      OR (src_entity_id = %s AND dst_entity_id = %s)) "
        "  RETURNING 1"
        ") SELECT count(*) AS n FROM affected",
        (user_id, loser_id, loser_id, winner_id, winner_id, loser_id),
    )

    duplicates_dropped = _affected(
        ms,
        # SCOPE-EXEMPT: per-user merge transaction (plan §2.11); every row is user_id-bound.
        "WITH affected AS ("
        "  DELETE FROM entity_edges e "
        "  WHERE e.user_id = %s AND e.valid_until IS NULL "
        "    AND ((e.src_entity_id = %s AND EXISTS ("
        "          SELECT 1 FROM entity_edges w WHERE w.user_id = e.user_id "
        "            AND w.bank = e.bank AND w.src_entity_id = %s "
        "            AND w.dst_entity_id = e.dst_entity_id "
        "            AND w.relation_type = e.relation_type AND w.valid_until IS NULL)) "
        "      OR (e.dst_entity_id = %s AND EXISTS ("
        "          SELECT 1 FROM entity_edges w WHERE w.user_id = e.user_id "
        "            AND w.bank = e.bank AND w.dst_entity_id = %s "
        "            AND w.src_entity_id = e.src_entity_id "
        "            AND w.relation_type = e.relation_type AND w.valid_until IS NULL))) "
        "  RETURNING 1"
        ") SELECT count(*) AS n FROM affected",
        (user_id, loser_id, winner_id, loser_id, winner_id),
    )

    # Two literal statements (src side, dst side) rather than one built from a
    # column name: the repo forbids composing SQL text, even from constants.
    edges_repointed = 0
    for sql in (
        # SCOPE-EXEMPT: per-user merge transaction (plan §2.11); every row is user_id-bound.
        "WITH affected AS ("
        "  UPDATE entity_edges e SET src_entity_id = %s, graph_projected_at = NULL, "
        "    reconcile_attempts = 0 "
        "  WHERE e.user_id = %s AND e.valid_until IS NULL AND e.src_entity_id = %s "
        "    AND NOT EXISTS ("
        "      SELECT 1 FROM entity_edges w WHERE w.user_id = e.user_id "
        "        AND w.bank = e.bank AND w.src_entity_id = %s "
        "        AND w.dst_entity_id = e.dst_entity_id "
        "        AND w.relation_type = e.relation_type) "
        "  RETURNING 1"
        ") SELECT count(*) AS n FROM affected",
        # SCOPE-EXEMPT: per-user merge transaction (plan §2.11); every row is user_id-bound.
        "WITH affected AS ("
        "  UPDATE entity_edges e SET dst_entity_id = %s, graph_projected_at = NULL, "
        "    reconcile_attempts = 0 "
        "  WHERE e.user_id = %s AND e.valid_until IS NULL AND e.dst_entity_id = %s "
        "    AND NOT EXISTS ("
        "      SELECT 1 FROM entity_edges w WHERE w.user_id = e.user_id "
        "        AND w.bank = e.bank AND w.dst_entity_id = %s "
        "        AND w.src_entity_id = e.src_entity_id "
        "        AND w.relation_type = e.relation_type) "
        "  RETURNING 1"
        ") SELECT count(*) AS n FROM affected",
    ):
        edges_repointed += _affected(ms, sql, (winner_id, user_id, loser_id, winner_id))

    edges_unmerged = _affected(
        ms,
        # SCOPE-EXEMPT: per-user merge transaction (plan §2.11); every row is user_id-bound.
        "SELECT count(*) AS n FROM entity_edges "
        "WHERE user_id = %s AND valid_until IS NULL "
        "  AND (src_entity_id = %s OR dst_entity_id = %s)",
        (user_id, loser_id, loser_id),
    )
    if edges_unmerged:
        _log.warning(
            "entity_merge: %d active entity_edges still reference loser=%s (collide with an "
            "archived winner edge); left untouched for temporal review (LUM-598)",
            edges_unmerged,
            loser_id,
        )
    return {
        "edges_repointed": edges_repointed,
        "edges_dropped_as_duplicate": duplicates_dropped,
        "edges_selfloop_archived": selfloops_archived,
        "edges_unmerged": edges_unmerged,
    }


def _run_phase_a(ms, winner_id: str, loser_id: str, user_id: str) -> dict:
    """Execute Phase A inside a single Postgres transaction.

    Returns a dict with relations_moved, sessions_updated, aliases_merged.
    Raises RuntimeError (wrapping the original exception) on any failure.
    Runs inside the caller's ``ms.transaction()``; commit and rollback belong
    to the caller.

    Every statement goes through the store (LUM-816). It used to take
    ``ms._conn`` — the process-wide connection — and drive raw cursors on it,
    which is the hazard this whole ticket exists to remove. Row counts now come
    from :func:`_affected` rather than ``cur.rowcount``, so nothing here needs a
    cursor and the port stays as narrow as it was.
    """

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
    relations_moved = _affected(
        ms,
        "WITH affected AS ("
        "  UPDATE entity_relations SET source_id = %s "
        "  WHERE source_id = %s AND user_id = %s "
        "    AND NOT EXISTS ("
        "      SELECT 1 FROM entity_relations er2 "
        "      WHERE er2.source_id = %s "
        "        AND er2.evidence_id = entity_relations.evidence_id "
        "        AND er2.relation_type = entity_relations.relation_type "
        "        AND er2.user_id = entity_relations.user_id"
        "    ) RETURNING 1"
        ") SELECT count(*) AS n FROM affected",
        (winner_id, loser_id, user_id, winner_id),
    )

    relations_dropped_as_duplicate = _affected(
        ms,
        "WITH affected AS ("
        "  DELETE FROM entity_relations WHERE source_id = %s AND user_id = %s RETURNING 1"
        ") SELECT count(*) AS n FROM affected",
        (loser_id, user_id),
    )

    # Step 3: Replace loser UUID in sessions.entity_ids arrays
    sessions_updated = _affected(
        ms,
        # The tag sits directly above the SQL, not above the call: the gate looks
        # back six lines from the `WHERE`, and wrapping the DML in a counting CTE
        # pushed it out of range — the gate working, not noise.
        # sessions.entity_ids is TEXT[] (migration 003): compare as text. The old
        # `::uuid` casts raised "uuid = text" on every real Postgres.
        # SCOPE-EXEMPT: per-user merge transaction (plan §2.11) — operates on the
        # owning user's session rows only; merges never cross user boundaries.
        "WITH affected AS ("
        "  UPDATE sessions "
        "  SET entity_ids = array_replace(entity_ids, %s, %s) "
        "  WHERE user_id = %s AND %s = ANY(entity_ids) RETURNING 1"
        ") SELECT count(*) AS n FROM affected",
        (loser_id, winner_id, user_id, loser_id),
    )

    # Step 4: Merge aliases, context_tags, mention_count onto winner
    winner_aliases = list(winner_row.get("aliases") or [])
    loser_aliases = list(loser_row.get("aliases") or [])
    winner_tags = list(winner_row.get("context_tags") or [])
    loser_tags = list(loser_row.get("context_tags") or [])

    merged_aliases = list(dict.fromkeys(winner_aliases + loser_aliases))
    merged_tags = list(dict.fromkeys(winner_tags + loser_tags))
    merged_count = (winner_row.get("mention_count") or 0) + (loser_row.get("mention_count") or 0)
    aliases_merged = len(merged_aliases) - len(winner_aliases)

    ms.execute(
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

    # Entity ids only ever appear in `entities.published_from`; the other tables'
    # `published_from` reference their own keys (file_index's is INTEGER, so the
    # full sweep raised "invalid input syntax for type integer" on live Postgres).
    remap_published_from(loser_id, winner_id, tables=("entities",))
    projections_remapped = True

    # Step 5c: Repoint typed ``entity_edges`` endpoints from loser to winner
    # (LUM-598). ``entity_edges`` has no FK to ``entities``, so the loser's
    # DELETE below leaves its edges naming an id that no longer exists: they
    # would match no graph node and never attach, forever. Edges are scoped to
    # ``user_id`` (all banks) and only ACTIVE rows (``valid_until IS NULL``)
    # move; archived rows are history and stay as written.
    edges = _repoint_entity_edges(ms, winner_id, loser_id, user_id)

    # Step 6: Delete loser entity (review_queue rows CASCADE)
    ms.execute(
        "DELETE FROM entities WHERE entity_id = %s AND user_id = %s",
        (loser_id, user_id),
    )

    return {
        "relations_moved": relations_moved,
        "sessions_updated": sessions_updated,
        "aliases_merged": max(0, aliases_merged),
        "relations_dropped_as_duplicate": relations_dropped_as_duplicate,
        "projections_remapped": projections_remapped,
        **edges,
    }


def _run_phase_b(
    winner_id: str, loser_id: str, user_id: str, winner_name: str, merged_aliases: list[str]
) -> bool:
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
            point_id = str(
                _uuid_mod.uuid5(
                    _uuid_mod.NAMESPACE_URL, f"entity::{user_id}::{winner_name.lower()}"
                )
            )
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

    phase_a: dict = {}
    winner_row_cached: dict = {}
    try:
        # Phase A — one transaction, on a connection this thread owns.
        #
        # This used to take `ms._conn` and toggle its `autocommit` by hand,
        # which re-implemented `transaction()` without even its guard, on the
        # connection every other thread was also using. `transaction()` is now
        # safe from any thread, so the hand-rolled version has no reason left.
        with ms.transaction():
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
        raise
    except Exception as exc:
        raise RuntimeError(
            f"entity_merge: Phase A failed for winner={winner_id} loser={loser_id}: {exc}"
        ) from exc

    # Fire ENTITY_MERGED hook (after commit, outside transaction)
    hooks.fire_background(
        Event.ENTITY_MERGED,
        winner_id=winner_id,
        loser_id=loser_id,
        user_id=user_id,
    )

    # Phase B — best-effort Qdrant cleanup
    winner_name = winner_row_cached.get("name", "")
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
