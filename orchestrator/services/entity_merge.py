# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Entity merge engine: merge, record, undo (ADR 234, LUM-965).

One engine for every merge (member, review queue, admin route, automatic
dedup). It has two halves so the member write path (``services/entity_edit``)
can run it inside its own transaction:

* :func:`merge_in_transaction` — everything that must be atomic. It takes the
  member's advisory lock (the one rename and kind change use), writes one
  ``entity_merges`` row and moves relations, sessions, names, projections and
  typed edges, recording **by id** what it touched, in the same statements
  ("record-in-place": ``WITH affected AS (… RETURNING id) UPDATE entity_merges
  SET col = (SELECT array_agg(id) FROM affected)``; ids never leave Postgres).
  It does not commit and fires nothing.
* :func:`after_merge_commit` — best-effort and never raises: the
  ``ENTITY_MERGED`` hook, the Qdrant points, the shared copies.

:func:`merge_entities` wraps both for callers that own no transaction.

Undo record
-----------
:func:`undo_in_transaction` replays a record in reverse. Undo is **newest
first per survivor**: a merge can be undone when it is the newest open merge
into its survivor; otherwise :class:`MergeNotNewest` names the newer one. That
is what makes each undo exact by construction: no later merge has moved rows
onto or off the restored state. The restored entry gets back its id, name,
other names, documents, links and shared copy; the survivor keeps what it
gained since. An undone pair is written to ``known_distinct_entity_pairs`` so
automatic dedup never merges the two again.

Merges made before migration 083 have no record and cannot be undone
(``CHANGELOG`` says so). The graph follows undo at the next reconcile pass
(``services/lumogis-graph/graph/reconcile.py``); there is no undo event.

Known residuals (disclosed in the guide)
* An undone record whose ``winner_id`` is later merged away is not re-pointed
  (only open records are), so the FK cascade removes that history row. History
  is lost; nothing restorable is.
* The loser's derived rows (``review_queue``, ``constraint_violations``,
  ``edge_scores``, ``dedup_candidates``) cascade-delete with it and are not
  restored; the next quality and dedup runs recompute them.
* A late ``ENTITY_MERGED`` webhook arriving after undo and reconcile would
  delete the restored graph node until its next change (seconds against a
  daily reconcile).

Import direction: ``entity_edit`` imports this module at the top; this module
imports ``entity_edit`` (lock constants, refresh helpers, exceptions) lazily
inside functions. ``test_entity_edit.py`` monkeypatches the helpers on
``entity_edit``, so they stay there.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Literal

import hooks
from events import Event
from models.entities import MergeResult

import config

_log = logging.getLogger(__name__)

Origin = Literal["member", "auto_dedup", "review_queue", "admin"]
ORIGINS = ("member", "auto_dedup", "review_queue", "admin")


class MergeChangesSharing(ValueError):
    """A non-member merge would change what the household sees."""


class MergeNotPersonal(ValueError):
    """Either row is not a personal source row (scope='personal', published_from NULL)."""


@dataclass
class MergeOutcome:
    merge_id: str
    winner_id: str
    loser_id: str
    user_id: str
    winner_row: dict
    loser_projection_scopes: list[str]
    relations_moved: int = 0
    relations_dropped: int = 0
    sessions_updated: int = 0
    aliases_merged: int = 0
    extra: dict = field(default_factory=dict)


@dataclass
class UndoOutcome:
    merge_id: str
    winner_id: str
    loser_id: str
    user_id: str
    restored_row: dict
    survivor_row: dict
    merged_at: Any
    moved_relation_ids: list[int]
    restored_scopes: list[str]
    winner_scopes_to_clear: list[str]
    skipped_rows: int = 0


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _n(ms, sql: str, params: tuple) -> int:
    """Run a complete statement that returns one row with column ``n``."""
    row = ms.fetch_one(sql, params)
    return int((row or {}).get("n") or 0)


def _kind(value: Any) -> str:
    # One definition of "the kind a name rule is keyed on" (LUM-966).
    from services.entity_name_rules import kind_key

    return kind_key(value)


def _dedupe_names(final_name: str, names: list[str]) -> list[str]:
    seen = {final_name.lower()}
    out: list[str] = []
    for raw in names:
        if not raw:
            continue
        if raw.lower() in seen:
            continue
        seen.add(raw.lower())
        out.append(raw)
    return out


def _rules_snapshot(ms, user_id: str, name_norm: str) -> list[dict]:
    rows = ms.fetch_all(
        # SCOPE-EXEMPT: entity_name_rules is per-member (no scope column); user_id-only.
        "SELECT from_type, action, to_type, restore_to_type, display_name, "
        "document_evidence, source_entity_id::text AS source_entity_id "
        "FROM entity_name_rules WHERE user_id = %s AND name_norm = %s ORDER BY from_type",
        (user_id, name_norm),
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Typed entity edges (LUM-598), now recording what they touched
# ---------------------------------------------------------------------------


def _repoint_entity_edges(ms, winner_id: str, loser_id: str, user_id: str, merge_id: str) -> dict:
    """Repoint active ``entity_edges`` endpoints from ``loser_id`` to ``winner_id``.

    Runs inside the merge transaction (LUM-598). Ordered statements, each a
    complete parameterised statement that also records the ids it touched on
    the merge record:

    1. **Self-loops are archived, not repointed** (``valid_until = now()``,
       the same soft-archive ``forget`` uses): a loser<->winner edge or an
       existing loser->loser edge would relate the survivor to itself.
       Recorded in ``edges_archived``.
    2. **Duplicates are dropped**: an active loser edge whose repointed key
       already exists as an ACTIVE winner edge. The whole row goes into
       ``edges_dropped`` so undo can put it back.
    3. **The rest are repointed** with ``graph_projected_at = NULL`` and
       ``reconcile_attempts = 0``. ``id``, ``evidence_id`` and the temporal
       columns do not change. Recorded in ``edges_moved_src`` / ``_dst``.
    4. **Stragglers are counted, not guessed**: an active loser edge that
       collides with an ARCHIVED winner edge is left alone (ADR 133/151).
    """
    selfloops_archived = _n(
        ms,
        # SCOPE-EXEMPT: per-user merge transaction (plan §2.11); every row is user_id-bound.
        "WITH affected AS ("
        "  UPDATE entity_edges SET valid_until = now() "
        "  WHERE user_id = %s AND valid_until IS NULL "
        "    AND ((src_entity_id = %s AND dst_entity_id IN (%s, %s)) "
        "      OR (src_entity_id = %s AND dst_entity_id = %s)) "
        "  RETURNING id"
        "), rec AS ("
        "  UPDATE entity_merges SET edges_archived = "
        "    coalesce((SELECT array_agg(id) FROM affected), '{}') "
        "  WHERE merge_id = %s RETURNING 1"
        ") SELECT count(*) AS n FROM affected",
        (user_id, loser_id, loser_id, winner_id, winner_id, loser_id, merge_id),
    )

    duplicates_dropped = _n(
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
        "  RETURNING to_jsonb(e) AS j"
        "), rec AS ("
        "  UPDATE entity_merges SET edges_dropped = "
        "    coalesce((SELECT jsonb_agg(j) FROM affected), '[]'::jsonb) "
        "  WHERE merge_id = %s RETURNING 1"
        ") SELECT count(*) AS n FROM affected",
        (user_id, loser_id, winner_id, loser_id, winner_id, merge_id),
    )

    # Two literal statements (src side, dst side) rather than one built from a
    # column name: the repo forbids composing SQL text, even from constants.
    moved_src = _n(
        ms,
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
        "  RETURNING e.id"
        "), rec AS ("
        "  UPDATE entity_merges SET edges_moved_src = "
        "    coalesce((SELECT array_agg(id) FROM affected), '{}') "
        "  WHERE merge_id = %s RETURNING 1"
        ") SELECT count(*) AS n FROM affected",
        (winner_id, user_id, loser_id, winner_id, merge_id),
    )
    moved_dst = _n(
        ms,
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
        "  RETURNING e.id"
        "), rec AS ("
        "  UPDATE entity_merges SET edges_moved_dst = "
        "    coalesce((SELECT array_agg(id) FROM affected), '{}') "
        "  WHERE merge_id = %s RETURNING 1"
        ") SELECT count(*) AS n FROM affected",
        (winner_id, user_id, loser_id, winner_id, merge_id),
    )

    edges_unmerged = _n(
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
        "edges_repointed": moved_src + moved_dst,
        "edges_dropped_as_duplicate": duplicates_dropped,
        "edges_selfloop_archived": selfloops_archived,
        "edges_unmerged": edges_unmerged,
    }


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

_ROWS_SQL = (
    "SELECT entity_id::text AS entity_id, name, entity_type, aliases, context_tags, "
    "       mention_count, scope, published_from::text AS published_from, is_staged, "
    "       version, user_id "
    # SCOPE-EXEMPT: the member's own rows by primary key and user_id, locked FOR UPDATE.
    "FROM entities WHERE entity_id = ANY(%s::uuid[]) AND user_id = %s "
    "ORDER BY entity_id FOR UPDATE"
)

_PROJECTION_SCOPES_SQL = (
    # SCOPE-EXEMPT: caller's own shared/system projections of a personal row.
    "SELECT scope FROM entities "
    "WHERE published_from = %s::uuid AND user_id = %s AND scope IN ('shared','system') "
    "ORDER BY scope"
)


def _take_lock(ms, user_id: str) -> None:
    """Per-member serialisation, the same lock rename and kind change take."""
    # Lazy: entity_edit imports this module at the top (see module docstring).
    from services import entity_edit

    ms.execute("SET LOCAL lock_timeout = '5s'")
    ms.fetch_one(entity_edit._LOCK_SQL, (entity_edit.ENTITY_NAMES_ADVISORY_KEY1, user_id))


def merge_in_transaction(
    ms,
    *,
    winner_id: str,
    loser_id: str,
    user_id: str,
    origin: str,
    acted_by: str | None,
    keep_name: str = "winner",
) -> MergeOutcome:
    """Phase A plus the undo record, inside the caller's transaction.

    Takes the member lock, does not commit and fires nothing. Raises
    ``ValueError`` (missing / staged), :class:`MergeNotPersonal` and
    :class:`MergeChangesSharing` before any write.
    """
    if winner_id == loser_id:
        raise ValueError(f"winner_id and loser_id must differ (got {winner_id!r})")
    if origin not in ORIGINS:
        raise ValueError(f"unknown merge origin {origin!r}")
    if keep_name not in ("winner", "loser"):
        raise ValueError(f"unknown keep_name {keep_name!r}")

    _take_lock(ms, user_id)

    rows = ms.fetch_all(_ROWS_SQL, ([winner_id, loser_id], user_id))
    by_id = {str(r["entity_id"]): r for r in rows}
    winner = by_id.get(str(winner_id))
    loser = by_id.get(str(loser_id))
    if winner is None:
        raise ValueError(f"winner entity {winner_id!r} not found for user_id={user_id!r}")
    if loser is None:
        raise ValueError(f"loser entity {loser_id!r} not found for user_id={user_id!r}")
    for row in (winner, loser):
        if row.get("scope") != "personal" or row.get("published_from") is not None:
            raise MergeNotPersonal(f"entity {row['entity_id']} is not a personal source row")
    if winner.get("is_staged") or loser.get("is_staged"):
        raise ValueError("staged entries cannot be merged")

    winner_scopes = [r["scope"] for r in ms.fetch_all(_PROJECTION_SCOPES_SQL, (winner_id, user_id))]
    loser_scopes = [r["scope"] for r in ms.fetch_all(_PROJECTION_SCOPES_SQL, (loser_id, user_id))]
    if origin != "member" and set(winner_scopes) != set(loser_scopes):
        raise MergeChangesSharing(
            f"merge of {loser_id} into {winner_id} would change what the household sees"
        )

    # The undo record. Every later statement fills one column of it.
    rec = ms.fetch_one(
        "INSERT INTO entity_merges (user_id, winner_id, loser_id, origin, acted_by, keep_name, "
        "                           loser_row, winner_before) "
        "SELECT %s, w.entity_id, l.entity_id, %s, %s, %s, to_jsonb(l), "
        "       jsonb_build_object('name', w.name, 'entity_type', w.entity_type, "
        "         'aliases', to_jsonb(w.aliases), 'context_tags', to_jsonb(w.context_tags), "
        "         'mention_count', w.mention_count) "
        # SCOPE-EXEMPT: the member's own two rows, by id and user_id.
        "FROM entities w, entities l WHERE w.entity_id = %s AND w.user_id = %s "
        "  AND l.entity_id = %s AND l.user_id = %s "
        "RETURNING merge_id::text AS merge_id",
        (user_id, origin, acted_by, keep_name, winner_id, user_id, loser_id, user_id),
    )
    merge_id = rec["merge_id"]

    # Open records whose survivor is the loser follow it to the winner, so they
    # stay undoable (the FK on winner_id would otherwise cascade them away).
    ms.fetch_one(
        "WITH affected AS ("
        "  UPDATE entity_merges SET winner_id = %s "
        # SCOPE-EXEMPT: entity_merges is per-member; user_id-bound.
        "  WHERE user_id = %s AND winner_id = %s AND undone_at IS NULL AND merge_id <> %s "
        "  RETURNING merge_id"
        "), rec AS ("
        "  UPDATE entity_merges SET records_remapped = "
        "    coalesce((SELECT array_agg(merge_id) FROM affected), '{}') "
        "  WHERE merge_id = %s RETURNING 1"
        ") SELECT count(*) AS n FROM affected",
        (winner_id, user_id, loser_id, merge_id, merge_id),
    )

    # Relations. Two-pass to respect UNIQUE(source_id, evidence_id, relation_type,
    # user_id): re-point what the winner does not already have, delete the rest
    # (the winner's first-observed created_at is kept by design). Both record ids.
    relations_moved = _n(
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
        "    ) RETURNING id"
        "), rec AS ("
        "  UPDATE entity_merges SET relations_moved = "
        "    coalesce((SELECT array_agg(id) FROM affected), '{}') "
        "  WHERE merge_id = %s RETURNING 1"
        ") SELECT count(*) AS n FROM affected",
        (winner_id, loser_id, user_id, winner_id, merge_id),
    )
    relations_dropped = _n(
        ms,
        "WITH affected AS ("
        "  DELETE FROM entity_relations WHERE source_id = %s AND user_id = %s RETURNING *"
        "), rec AS ("
        "  UPDATE entity_merges SET relations_dropped = "
        "    coalesce((SELECT jsonb_agg(to_jsonb(a)) FROM affected a), '[]'::jsonb) "
        "  WHERE merge_id = %s RETURNING 1"
        ") SELECT count(*) AS n FROM affected",
        (loser_id, user_id, merge_id),
    )

    # Sessions: record which contained the loser (and whether the winner was
    # already there), then replace. sessions.entity_ids is TEXT[] (migration 003).
    ms.execute(
        "UPDATE entity_merges SET sessions = coalesce(("
        "  SELECT jsonb_agg(jsonb_build_object("
        "    'session_id', s.session_id::text, 'had_winner', %s = ANY(s.entity_ids))) "
        # SCOPE-EXEMPT: per-user merge transaction; sessions of the owning user only.
        "  FROM sessions s WHERE s.user_id = %s AND %s = ANY(s.entity_ids)), '[]'::jsonb) "
        "WHERE merge_id = %s",
        (winner_id, user_id, loser_id, merge_id),
    )
    sessions_updated = _n(
        ms,
        # SCOPE-EXEMPT: per-user merge transaction (plan §2.11) — operates on the
        # owning user's session rows only; merges never cross user boundaries.
        "WITH affected AS ("
        "  UPDATE sessions "
        "  SET entity_ids = array_replace(entity_ids, %s, %s) "
        "  WHERE user_id = %s AND %s = ANY(entity_ids) RETURNING 1"
        ") SELECT count(*) AS n FROM affected",
        (loser_id, winner_id, user_id, loser_id),
    )

    # Names. The loser's name becomes one of the winner's other names (otherwise
    # the next extraction of "Dr. A. Meier" would create the duplicate again).
    winner_aliases = list(winner.get("aliases") or [])
    loser_aliases = list(loser.get("aliases") or [])
    if keep_name == "winner":
        final_name = winner["name"]
        candidates = winner_aliases + [loser["name"]] + loser_aliases
    else:
        final_name = loser["name"]
        candidates = winner_aliases + [winner["name"]] + loser_aliases
    final_aliases = _dedupe_names(final_name, candidates)
    before_aliases = {a.lower() for a in winner_aliases}
    aliases_added = [a for a in final_aliases if a.lower() not in before_aliases]
    winner_tags = list(winner.get("context_tags") or [])
    tags_added = [
        t for t in dict.fromkeys(loser.get("context_tags") or []) if t not in set(winner_tags)
    ]
    final_tags = winner_tags + tags_added
    name_set = final_name if final_name != winner["name"] else None

    ms.execute(
        "UPDATE entity_merges SET name_set = %s, aliases_added = %s, tags_added = %s "
        "WHERE merge_id = %s",
        (name_set, aliases_added, tags_added, merge_id),
    )
    ms.execute(
        "UPDATE entities SET name = %s, aliases = %s, context_tags = %s, "
        "  mention_count = mention_count + %s, version = version + 1, "
        "  graph_projected_at = NULL, updated_at = NOW() "
        # SCOPE-EXEMPT: the winner is the caller's own personal row, locked above.
        "WHERE entity_id = %s AND user_id = %s",
        (
            final_name,
            final_aliases,
            final_tags,
            int(loser.get("mention_count") or 0),
            winner_id,
            user_id,
        ),
    )

    # A cross-kind merge teaches extraction that the loser's name is the survivor's kind.
    from services import entity_name_rules

    loser_kind = _kind(loser.get("entity_type"))
    winner_kind = _kind(winner.get("entity_type"))
    key = entity_name_rules.name_key(loser["name"])
    # NOTE (LUM-966, residual 10): only the loser's canonical name is lifted from a
    # block, through record_retype step 1b. Its aliases are not: lifting them would
    # make this single-key snapshot multi-key for undo. Rare; the member removes
    # such a block on the "won't learn" page.
    if loser_kind != winner_kind and key:
        before = _rules_snapshot(ms, user_id, key)
        entity_name_rules.record_retype(
            ms,
            user_id=user_id,
            name=loser["name"],
            from_type=loser_kind,
            to_type=winner_kind,
            source_entity_id=winner_id,
        )
        after = _rules_snapshot(ms, user_id, key)
        ms.execute(
            "UPDATE entity_merges SET name_rules = %s::jsonb WHERE merge_id = %s",
            (json.dumps({"name_norm": key, "before": before, "after": after}), merge_id),
        )

    # Distinct pairs: the loser's pairs are carried to the winner; undo reverses it.
    ms.execute(
        "UPDATE entity_merges SET distinct_pairs_lost = coalesce(("
        "  SELECT jsonb_agg(jsonb_build_object('a', entity_id_a::text, 'b', entity_id_b::text)) "
        # SCOPE-EXEMPT: known_distinct_entity_pairs is per-member (no scope column).
        "  FROM known_distinct_entity_pairs p WHERE p.user_id = %s "
        "    AND (p.entity_id_a = %s::uuid OR p.entity_id_b = %s::uuid)), '[]'::jsonb) "
        "WHERE merge_id = %s",
        (user_id, loser_id, loser_id, merge_id),
    )
    ms.fetch_one(
        "WITH ins AS ("
        "  INSERT INTO known_distinct_entity_pairs (user_id, entity_id_a, entity_id_b) "
        "  SELECT %s, LEAST(o.other, %s::uuid), GREATEST(o.other, %s::uuid) FROM ("
        "    SELECT CASE WHEN p.entity_id_a = %s::uuid THEN p.entity_id_b "
        "                ELSE p.entity_id_a END AS other "
        # SCOPE-EXEMPT: known_distinct_entity_pairs is per-member (no scope column).
        "    FROM known_distinct_entity_pairs p WHERE p.user_id = %s "
        "      AND (p.entity_id_a = %s::uuid OR p.entity_id_b = %s::uuid)) o "
        "  WHERE o.other <> %s::uuid "
        "  ON CONFLICT DO NOTHING RETURNING entity_id_a, entity_id_b"
        "), rec AS ("
        "  UPDATE entity_merges SET distinct_pairs_added = coalesce(("
        "    SELECT jsonb_agg(jsonb_build_object("
        "      'a', entity_id_a::text, 'b', entity_id_b::text)) FROM ins), '[]'::jsonb) "
        "  WHERE merge_id = %s RETURNING 1"
        ") SELECT count(*) AS n FROM ins",
        (user_id, winner_id, winner_id, loser_id, user_id, loser_id, loser_id, winner_id, merge_id),
    )

    # Shared/system projections re-point to the survivor BEFORE the loser is
    # deleted (every projection FK is ON DELETE CASCADE). Same two statements as
    # ``projection.remap_published_from(tables=("entities",))``, inline so the
    # record can capture the rows (the entity merge was its only caller).
    _n(
        ms,
        "WITH affected AS ("
        "  DELETE FROM entities t "
        # SCOPE-EXEMPT: the member's own projection rows (published_from + user_id).
        "  WHERE t.published_from = %s AND t.user_id = %s "
        "    AND EXISTS (SELECT 1 FROM entities sib "
        "                WHERE sib.published_from = %s AND sib.scope = t.scope) "
        "  RETURNING t.*"
        "), rec AS ("
        "  UPDATE entity_merges SET projections_dropped = "
        "    coalesce((SELECT jsonb_agg(to_jsonb(a)) FROM affected a), '[]'::jsonb) "
        "  WHERE merge_id = %s RETURNING 1"
        ") SELECT count(*) AS n FROM affected",
        (loser_id, user_id, winner_id, merge_id),
    )
    _n(
        ms,
        "WITH affected AS ("
        "  UPDATE entities SET published_from = %s, graph_projected_at = NULL "
        # SCOPE-EXEMPT: the member's own projection rows (published_from + user_id).
        "  WHERE published_from = %s AND user_id = %s RETURNING entity_id"
        "), rec AS ("
        "  UPDATE entity_merges SET projections_moved = "
        "    coalesce((SELECT array_agg(entity_id) FROM affected), '{}') "
        "  WHERE merge_id = %s RETURNING 1"
        ") SELECT count(*) AS n FROM affected",
        (winner_id, loser_id, user_id, merge_id),
    )
    leftover = ms.fetch_one(
        "SELECT 1 AS x FROM entities WHERE published_from = %s LIMIT 1",  # postcondition
        (loser_id,),
    )
    if leftover is not None:
        raise RuntimeError(f"projection remap left rows pointing at loser {loser_id}")

    edges = _repoint_entity_edges(ms, winner_id, loser_id, user_id, merge_id)

    # Delete the loser (review_queue, constraint_violations, edge_scores,
    # dedup_candidates and its distinct pairs cascade; all are derived or recorded).
    ms.execute(
        "DELETE FROM entities WHERE entity_id = %s AND user_id = %s",
        (loser_id, user_id),
    )

    winner_row = ms.fetch_one(
        # SCOPE-EXEMPT: the member's own winner row.
        "SELECT * FROM entities WHERE entity_id = %s AND user_id = %s",
        (winner_id, user_id),
    )
    if winner_row is not None:
        winner_row["entity_id"] = str(winner_row["entity_id"])
    size = ms.fetch_one(
        "SELECT pg_column_size(m.*) AS bytes FROM entity_merges m WHERE merge_id = %s",
        (merge_id,),
    )
    _log.info(
        "entity_merge: recorded merge_id=%s origin=%s winner=%s loser=%s relations_moved=%d "
        "relations_dropped=%d sessions_updated=%d aliases_added=%d record_bytes=%s",
        merge_id,
        origin,
        winner_id,
        loser_id,
        relations_moved,
        relations_dropped,
        sessions_updated,
        len(aliases_added),
        (size or {}).get("bytes"),
    )
    return MergeOutcome(
        merge_id=merge_id,
        winner_id=str(winner_id),
        loser_id=str(loser_id),
        user_id=user_id,
        winner_row=winner_row or {},
        loser_projection_scopes=loser_scopes,
        relations_moved=relations_moved,
        relations_dropped=relations_dropped,
        sessions_updated=sessions_updated,
        aliases_merged=len(aliases_added),
        extra=edges,
    )


def _delete_loser_point(loser_id: str) -> bool:
    try:
        config.get_vector_store().delete_where(
            collection="entities",
            filter={"must": [{"key": "entity_id", "match": {"value": loser_id}}]},
        )
        return True
    except Exception:  # noqa: BLE001
        _log.exception(
            "entity_merge: Phase B — failed to delete loser Qdrant point loser_id=%s", loser_id
        )
        return False


def after_merge_commit(outcome: MergeOutcome) -> bool:
    """Everything that follows a committed merge. Best-effort, never raises.

    Fires ``ENTITY_MERGED`` (the graph webhook), deletes the loser's personal
    point, re-embeds the winner and deletes the loser-derived shared points
    before refreshing the winner's shared copies. Returns ``qdrant_cleaned``.
    """
    from auth import UserContext
    from services.projection import projection_point_id

    from services import entity_edit

    hooks.fire_background(
        Event.ENTITY_MERGED,
        winner_id=outcome.winner_id,
        loser_id=outcome.loser_id,
        user_id=outcome.user_id,
    )
    ok = _delete_loser_point(outcome.loser_id)
    try:
        vs = config.get_vector_store()
        for scope in outcome.loser_projection_scopes:
            try:
                vs.delete("entities", projection_point_id("entities", outcome.loser_id, scope))
            except Exception:  # noqa: BLE001
                ok = False
                _log.exception(
                    "entity_merge: failed to delete loser shared point loser=%s scope=%s",
                    outcome.loser_id,
                    scope,
                )
    except Exception:  # noqa: BLE001
        ok = False
        _log.exception("entity_merge: vector store unavailable loser=%s", outcome.loser_id)
    try:
        ok = bool(entity_edit._refresh_search_point(outcome.winner_row)) and ok
        entity_edit._refresh_shared_projections(
            outcome.winner_row, UserContext(user_id=outcome.user_id, is_authenticated=True)
        )
    except Exception:  # noqa: BLE001
        ok = False
        _log.exception("entity_merge: post-commit refresh failed winner=%s", outcome.winner_id)
    if not ok:
        _log.error(
            "entity_merge: Phase B failed — Qdrant not fully synced winner=%s loser=%s "
            "(Postgres commit stands)",
            outcome.winner_id,
            outcome.loser_id,
        )
    return ok


def merge_entities(
    winner_id: str,
    loser_id: str,
    user_id: str,
    *,
    origin: str,
    acted_by: str | None = None,
    keep_name: str = "winner",
) -> MergeResult:
    """Merge loser entity into winner: one transaction, then the post-commit half.

    ``origin`` is required (no default) so no caller silently records a wrong one.
    Raises ``ValueError`` (same id, missing or staged), :class:`MergeNotPersonal`,
    :class:`MergeChangesSharing` (non-member origin), ``RuntimeError`` on a SQL
    failure (rolled back, nothing fired). A lock wait over 5 s propagates as
    ``psycopg2.errors.LockNotAvailable`` (dedup treats that as "try next run").
    """
    if winner_id == loser_id:
        raise ValueError(f"winner_id and loser_id must differ (got {winner_id!r})")

    import psycopg2

    ms = config.get_metadata_store()
    try:
        with ms.transaction():
            outcome = merge_in_transaction(
                ms,
                winner_id=winner_id,
                loser_id=loser_id,
                user_id=user_id,
                origin=origin,
                acted_by=acted_by,
                keep_name=keep_name,
            )
    except (ValueError, psycopg2.errors.LockNotAvailable):
        raise
    except Exception as exc:
        raise RuntimeError(
            f"entity_merge: Phase A failed for winner={winner_id} loser={loser_id}: {exc}"
        ) from exc

    qdrant_cleaned = after_merge_commit(outcome)
    return MergeResult(
        winner_id=outcome.winner_id,
        loser_id=outcome.loser_id,
        aliases_merged=outcome.aliases_merged,
        relations_moved=outcome.relations_moved,
        sessions_updated=outcome.sessions_updated,
        qdrant_cleaned=qdrant_cleaned,
        merge_id=outcome.merge_id,
    )


# ---------------------------------------------------------------------------
# Move mentions (also used by LUM-968 split)
# ---------------------------------------------------------------------------


def move_mentions_in_transaction(
    ms,
    *,
    from_id: str,
    to_id: str,
    relation_ids: list[int],
    user_id: str,
) -> int:
    """Move ``entity_relations`` rows (by id) from one entry to another.

    Inside the caller's transaction. Same two-pass collision rule as merge:
    re-point where ``to`` has no ``(evidence_id, relation_type)``, delete the
    rest. Returns the number of rows that left ``from_id``. Raises
    :class:`services.entity_edit.MentionsNotFound` when any id is not a row of
    ``from_id`` for this member (nothing is written).
    """
    from services import entity_edit

    ids = sorted({int(i) for i in relation_ids})
    _take_lock(ms, user_id)
    have = _n(
        ms,
        "SELECT count(*) AS n FROM entity_relations "
        "WHERE id = ANY(%s) AND source_id = %s AND user_id = %s",
        (ids, from_id, user_id),
    )
    if have != len(ids):
        raise entity_edit.MentionsNotFound()

    sessions = ms.fetch_all(
        "SELECT DISTINCT evidence_id FROM entity_relations "
        "WHERE id = ANY(%s) AND source_id = %s AND user_id = %s AND evidence_type = 'SESSION'",
        (ids, from_id, user_id),
    )
    moved = _n(
        ms,
        "WITH affected AS ("
        "  UPDATE entity_relations SET source_id = %s "
        "  WHERE id = ANY(%s) AND source_id = %s AND user_id = %s "
        "    AND NOT EXISTS ("
        "      SELECT 1 FROM entity_relations er2 WHERE er2.source_id = %s "
        "        AND er2.evidence_id = entity_relations.evidence_id "
        "        AND er2.relation_type = entity_relations.relation_type "
        "        AND er2.user_id = entity_relations.user_id) RETURNING 1"
        ") SELECT count(*) AS n FROM affected",
        (to_id, ids, from_id, user_id, to_id),
    )
    dropped = _n(
        ms,
        "WITH affected AS ("
        "  DELETE FROM entity_relations WHERE id = ANY(%s) AND source_id = %s AND user_id = %s "
        "  RETURNING 1) SELECT count(*) AS n FROM affected",
        (ids, from_id, user_id),
    )
    for srow in sessions:
        sid = srow["evidence_id"]
        ms.execute(
            # SCOPE-EXEMPT: per-user transaction; sessions of the owning user only.
            "UPDATE sessions SET entity_ids = CASE WHEN %s = ANY(entity_ids) "
            "    THEN entity_ids ELSE array_append(entity_ids, %s) END, "
            "  graph_projected_at = NULL "
            "WHERE user_id = %s AND session_id::text = %s",
            (to_id, to_id, user_id, sid),
        )
        ms.execute(
            # SCOPE-EXEMPT: per-user transaction; sessions of the owning user only.
            "UPDATE sessions SET entity_ids = array_remove(entity_ids, %s), "
            "  graph_projected_at = NULL "
            "WHERE user_id = %s AND session_id::text = %s "
            "  AND NOT EXISTS (SELECT 1 FROM entity_relations er WHERE er.source_id = %s "
            "    AND er.user_id = %s AND er.evidence_type = 'SESSION' AND er.evidence_id = %s)",
            (from_id, user_id, sid, from_id, user_id, sid),
        )
    n = moved + dropped
    ms.execute(
        "UPDATE entities SET mention_count = GREATEST(1, mention_count - %s), "
        "  version = version + 1, graph_projected_at = NULL, updated_at = NOW() "
        # SCOPE-EXEMPT: the member's own personal source row.
        "WHERE entity_id = %s AND user_id = %s",
        (n, from_id, user_id),
    )
    ms.execute(
        "UPDATE entities SET mention_count = mention_count + %s, "
        "  version = version + 1, graph_projected_at = NULL, updated_at = NOW() "
        # SCOPE-EXEMPT: the member's own personal source row.
        "WHERE entity_id = %s AND user_id = %s",
        (moved, to_id, user_id),
    )
    return n


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------

_RECORD_SQL = (
    "SELECT merge_id::text AS merge_id, winner_id::text AS winner_id, loser_id::text AS loser_id, "
    "       merged_at, undone_at, name_set, aliases_added, tags_added, "
    "       relations_moved, cardinality(relations_moved) AS n_moved, "
    "       jsonb_array_length(relations_dropped) AS n_dropped, "
    "       edges_moved_src, edges_moved_dst, edges_archived, "
    "       jsonb_array_length(edges_dropped) AS n_edges_dropped, "
    "       cardinality(edges_moved_src) + cardinality(edges_moved_dst) AS n_edges_moved, "
    "       cardinality(edges_archived) AS n_edges_archived, "
    "       projections_moved::text[] AS projections_moved, name_rules, "
    "       jsonb_array_length(projections_dropped) AS n_projections_dropped, "
    "       records_remapped::text[] AS records_remapped, "
    "       (loser_row->>'mention_count')::int AS loser_mentions, "
    "       winner_before "
    # SCOPE-EXEMPT: entity_merges is per-member (no scope column); user_id-bound.
    "FROM entity_merges WHERE merge_id = %s::uuid AND user_id = %s FOR UPDATE"
)


def undo_in_transaction(ms, *, merge_id: str, user_id: str, acted_by: str | None) -> UndoOutcome:
    """Replay a merge record in reverse, inside the caller's transaction.

    Raises :class:`services.entity_edit.MergeNotFound` (unknown, malformed or
    another member's record), ``MergeAlreadyUndone``, ``MergeNotNewest`` and
    ``MergeLoserIdTaken`` before any write. Every replay statement is guarded
    by "still where the merge left it" and counted into ``skipped_rows``.
    """
    from services import entity_edit

    _take_lock(ms, user_id)
    try:
        rec = ms.fetch_one(_RECORD_SQL, (merge_id, user_id))
    except Exception as exc:  # noqa: BLE001 - malformed uuid text is a "not found"
        if "invalid input syntax for type uuid" in str(exc):
            raise entity_edit.MergeNotFound(merge_id) from exc
        raise
    if rec is None:
        raise entity_edit.MergeNotFound(merge_id)
    if rec["undone_at"] is not None:
        raise entity_edit.MergeAlreadyUndone(merge_id)
    w = rec["winner_id"]
    loser = rec["loser_id"]

    newer = ms.fetch_one(
        "SELECT m.merge_id::text AS merge_id, m.loser_row->>'name' AS other_name "
        # SCOPE-EXEMPT: entity_merges is per-member (no scope column); user_id-bound.
        "FROM entity_merges m, entity_merges r "
        "WHERE r.merge_id = %s::uuid AND m.user_id = %s AND m.winner_id = r.winner_id "
        "  AND m.undone_at IS NULL AND (m.merged_at, m.merge_id) > (r.merged_at, r.merge_id) "
        "ORDER BY m.merged_at DESC, m.merge_id DESC LIMIT 1",
        (merge_id, user_id),
    )
    if newer is not None:
        raise entity_edit.MergeNotNewest(newer["merge_id"], newer["other_name"] or "")

    if ms.fetch_one("SELECT 1 AS x FROM entities WHERE entity_id = %s::uuid", (loser,)):
        raise entity_edit.MergeLoserIdTaken(loser)

    skipped = 0

    # The loser row, from the snapshot. user_id always comes from the record's
    # owner: JSONB copies keep whatever user_id was current at merge time, and
    # the default-user remap rewrites columns, not JSONB.
    ms.execute(
        "INSERT INTO entities "
        "SELECT (jsonb_populate_record(NULL::entities, "
        "         m.loser_row || jsonb_build_object('user_id', %s::text))).* "
        # SCOPE-EXEMPT: entity_merges is per-member (no scope column); user_id-bound.
        "FROM entity_merges m WHERE m.merge_id = %s::uuid AND m.user_id = %s",
        (user_id, merge_id, user_id),
    )
    ms.execute(
        "UPDATE entities SET version = version + 1, graph_projected_at = NULL, "
        "  updated_at = NOW() "
        # SCOPE-EXEMPT: the member's own restored personal row.
        "WHERE entity_id = %s::uuid AND user_id = %s",
        (loser, user_id),
    )

    # Relations: move back what is still on the survivor and whose slot on the
    # loser is free; re-insert the dropped rows while their evidence is live.
    moved_back = _n(
        ms,
        # SCOPE-EXEMPT: per-user undo transaction; every row is user_id-bound.
        "WITH affected AS ("
        "  UPDATE entity_relations r SET source_id = %s::uuid "
        "  WHERE r.user_id = %s AND r.id = ANY(%s) AND r.source_id = %s::uuid "
        "    AND NOT EXISTS (SELECT 1 FROM entity_relations x WHERE x.source_id = %s::uuid "
        "      AND x.evidence_id = r.evidence_id AND x.relation_type = r.relation_type "
        "      AND x.user_id = r.user_id) RETURNING 1"
        ") SELECT count(*) AS n FROM affected",
        (loser, user_id, list(rec["relations_moved"] or []), w, loser),
    )
    reinserted = _n(
        ms,
        "WITH ins AS ("
        "  INSERT INTO entity_relations "
        "  SELECT (jsonb_populate_record(NULL::entity_relations, "
        "           r || jsonb_build_object('user_id', %s::text, 'source_id', %s::text))).* "
        # SCOPE-EXEMPT: entity_merges is per-member (no scope column); user_id-bound.
        "  FROM entity_merges m, jsonb_array_elements(m.relations_dropped) r "
        "  WHERE m.merge_id = %s::uuid AND m.user_id = %s "
        "    AND EXISTS (SELECT 1 FROM entity_relations live WHERE live.user_id = %s "
        "      AND live.evidence_id = r->>'evidence_id' "
        "      AND live.relation_type = r->>'relation_type') "
        "  ON CONFLICT DO NOTHING RETURNING 1"
        ") SELECT count(*) AS n FROM ins",
        (user_id, loser, merge_id, user_id, user_id),
    )
    skipped += (rec["n_moved"] - moved_back) + (rec["n_dropped"] - reinserted)

    # Sessions: the loser comes back; the survivor leaves where it was only there
    # because of the loser and has no other evidence for that session.
    ms.execute(
        # SCOPE-EXEMPT: per-user transaction; sessions of the owning user only.
        "UPDATE sessions SET entity_ids = array_append(entity_ids, %s), graph_projected_at = NULL "
        "WHERE user_id = %s AND NOT (%s = ANY(entity_ids)) AND session_id::text IN ("
        "  SELECT x->>'session_id' FROM entity_merges m, jsonb_array_elements(m.sessions) x "
        "  WHERE m.merge_id = %s::uuid AND m.user_id = %s)",
        (loser, user_id, loser, merge_id, user_id),
    )
    ms.execute(
        # SCOPE-EXEMPT: per-user transaction; sessions of the owning user only.
        "UPDATE sessions SET entity_ids = array_remove(entity_ids, %s), graph_projected_at = NULL "
        "WHERE user_id = %s AND %s = ANY(entity_ids) AND session_id::text IN ("
        "  SELECT x->>'session_id' FROM entity_merges m, jsonb_array_elements(m.sessions) x "
        "  WHERE m.merge_id = %s::uuid AND m.user_id = %s AND (x->>'had_winner')::boolean = false) "
        "  AND NOT EXISTS (SELECT 1 FROM entity_relations er WHERE er.source_id = %s::uuid "
        "    AND er.user_id = %s AND er.evidence_type = 'SESSION' "
        "    AND er.evidence_id = sessions.session_id::text)",
        (w, user_id, w, merge_id, user_id, w, user_id),
    )

    # The survivor: take back what the merge added, keep what it gained since.
    cur = ms.fetch_one(
        "SELECT name, aliases, context_tags, mention_count FROM entities "
        # SCOPE-EXEMPT: the member's own survivor row.
        "WHERE entity_id = %s::uuid AND user_id = %s FOR UPDATE",
        (w, user_id),
    )
    if cur is None:
        raise entity_edit.MergeNotFound(merge_id)
    before = rec["winner_before"] or {}
    cur_name = cur["name"]
    name = cur_name
    if rec["name_set"] is not None and cur_name == rec["name_set"]:
        name = before.get("name") or cur_name
    # The merge can also have *removed* a form from the survivor: with
    # ``keep_name='loser'`` the winner's other name that equalled the loser's name
    # became the entry's name. Put such forms back, and never strip the survivor's
    # own pre-merge name when it is not the name any more (a rename since).
    before_aliases = [a for a in (before.get("aliases") or []) if a]
    own_forms = {a.lower() for a in before_aliases}
    if before.get("name"):
        own_forms.add(before["name"].lower())
    drop = ({a.lower() for a in (rec["aliases_added"] or [])} - own_forms) | {name.lower()}
    aliases = [a for a in (cur.get("aliases") or []) if a.lower() not in drop]
    if name != cur_name:  # the name is being reverted: its old forms come back
        have = {a.lower() for a in aliases} | {name.lower()}
        for a in before_aliases:
            if a.lower() not in have:
                aliases.append(a)
                have.add(a.lower())
    tags_drop = set(rec["tags_added"] or [])
    tags = [t for t in (cur.get("context_tags") or []) if t not in tags_drop]
    count = max(
        int(cur.get("mention_count") or 0) - int(rec["loser_mentions"] or 0),
        int(before.get("mention_count") or 0),
    )
    ms.execute(
        "UPDATE entities SET name = %s, aliases = %s, context_tags = %s, mention_count = %s, "
        "  version = version + 1, graph_projected_at = NULL, updated_at = NOW() "
        # SCOPE-EXEMPT: the member's own survivor row.
        "WHERE entity_id = %s::uuid AND user_id = %s",
        (name, aliases, tags, count, w, user_id),
    )

    # Typed edges.
    moved_src = _n(
        ms,
        # SCOPE-EXEMPT: per-user undo transaction; every row is user_id-bound.
        "WITH affected AS ("
        "  UPDATE entity_edges e SET src_entity_id = %s, graph_projected_at = NULL, "
        "    reconcile_attempts = 0 "
        "  WHERE e.user_id = %s AND e.id = ANY(%s) AND e.src_entity_id = %s "
        "    AND e.valid_until IS NULL AND NOT EXISTS (SELECT 1 FROM entity_edges x "
        "      WHERE x.user_id = e.user_id AND x.bank = e.bank AND x.src_entity_id = %s "
        "        AND x.dst_entity_id = e.dst_entity_id AND x.relation_type = e.relation_type) "
        "  RETURNING 1) SELECT count(*) AS n FROM affected",
        (loser, user_id, list(rec["edges_moved_src"] or []), w, loser),
    )
    moved_dst = _n(
        ms,
        # SCOPE-EXEMPT: per-user undo transaction; every row is user_id-bound.
        "WITH affected AS ("
        "  UPDATE entity_edges e SET dst_entity_id = %s, graph_projected_at = NULL, "
        "    reconcile_attempts = 0 "
        "  WHERE e.user_id = %s AND e.id = ANY(%s) AND e.dst_entity_id = %s "
        "    AND e.valid_until IS NULL AND NOT EXISTS (SELECT 1 FROM entity_edges x "
        "      WHERE x.user_id = e.user_id AND x.bank = e.bank AND x.dst_entity_id = %s "
        "        AND x.src_entity_id = e.src_entity_id AND x.relation_type = e.relation_type) "
        "  RETURNING 1) SELECT count(*) AS n FROM affected",
        (loser, user_id, list(rec["edges_moved_dst"] or []), w, loser),
    )
    unarchived = _n(
        ms,
        # SCOPE-EXEMPT: per-user undo transaction; every row is user_id-bound.
        "WITH affected AS ("
        "  UPDATE entity_edges SET valid_until = NULL, graph_projected_at = NULL, "
        "    reconcile_attempts = 0 "
        "  WHERE user_id = %s AND id = ANY(%s) AND valid_until IS NOT NULL "
        "  RETURNING 1) SELECT count(*) AS n FROM affected",
        (user_id, list(rec["edges_archived"] or [])),
    )
    edges_back = _n(
        ms,
        "WITH ins AS ("
        "  INSERT INTO entity_edges "
        "  SELECT (jsonb_populate_record(NULL::entity_edges, "
        "           e || jsonb_build_object('user_id', %s::text, "
        "                                   'graph_projected_at', NULL))).* "
        # SCOPE-EXEMPT: entity_merges is per-member (no scope column); user_id-bound.
        "  FROM entity_merges m, jsonb_array_elements(m.edges_dropped) e "
        "  WHERE m.merge_id = %s::uuid AND m.user_id = %s "
        # entity_edges has no FK: an endpoint deleted since the merge (LUM-966)
        # must not come back as a dangling edge.
        # SCOPE-EXEMPT: the member's own personal endpoints, user_id-bound.
        "    AND EXISTS (SELECT 1 FROM entities x "
        "                WHERE x.user_id = %s AND x.entity_id::text = e->>'src_entity_id') "
        "    AND EXISTS (SELECT 1 FROM entities x "
        "                WHERE x.user_id = %s AND x.entity_id::text = e->>'dst_entity_id') "
        "  ON CONFLICT DO NOTHING RETURNING 1) SELECT count(*) AS n FROM ins",
        (user_id, merge_id, user_id, user_id, user_id),
    )
    skipped += (rec["n_edges_moved"] - moved_src - moved_dst) + (
        rec["n_edges_archived"] - unarchived
    )
    skipped += rec["n_edges_dropped"] - edges_back

    # Projections: moved copies go back; dropped copies return verbatim (own id,
    # own published_from), so undo never shares an entry the member did not share.
    # A copy the member unshared since (moved row gone) or a re-used slot
    # (dropped row conflicts) is not put back and counts in skipped_rows.
    projections_moved = list(rec["projections_moved"] or [])
    projections_back = _n(
        ms,
        "WITH affected AS ("
        "  UPDATE entities SET published_from = %s::uuid, graph_projected_at = NULL, "
        "    updated_at = NOW() "
        # SCOPE-EXEMPT: the member's own projection rows (user_id + id list).
        "  WHERE user_id = %s AND entity_id = ANY(%s::uuid[]) AND published_from = %s::uuid "
        "  RETURNING 1) SELECT count(*) AS n FROM affected",
        (loser, user_id, projections_moved, w),
    )
    projections_reinserted = _n(
        ms,
        "WITH ins AS ("
        "  INSERT INTO entities "
        "  SELECT (jsonb_populate_record(NULL::entities, "
        "           p || jsonb_build_object('user_id', %s::text, 'graph_projected_at', NULL))).* "
        # SCOPE-EXEMPT: entity_merges is per-member (no scope column); user_id-bound.
        "  FROM entity_merges m, jsonb_array_elements(m.projections_dropped) p "
        "  WHERE m.merge_id = %s::uuid AND m.user_id = %s "
        "  ON CONFLICT DO NOTHING RETURNING 1) SELECT count(*) AS n FROM ins",
        (user_id, merge_id, user_id),
    )
    skipped += (len(projections_moved) - projections_back) + (
        int(rec.get("n_projections_dropped") or 0) - projections_reinserted
    )

    # Distinct pairs.
    ms.execute(
        "DELETE FROM known_distinct_entity_pairs d USING entity_merges m, "
        "  jsonb_array_elements(m.distinct_pairs_added) x "
        # SCOPE-EXEMPT: known_distinct_entity_pairs is per-member (no scope column).
        "WHERE m.merge_id = %s::uuid AND d.user_id = %s "
        "  AND d.entity_id_a = (x->>'a')::uuid AND d.entity_id_b = (x->>'b')::uuid",
        (merge_id, user_id),
    )
    ms.execute(
        "INSERT INTO known_distinct_entity_pairs (user_id, entity_id_a, entity_id_b) "
        "SELECT %s, (x->>'a')::uuid, (x->>'b')::uuid "
        # SCOPE-EXEMPT: entity_merges is per-member (no scope column); user_id-bound.
        "FROM entity_merges m, jsonb_array_elements(m.distinct_pairs_lost) x "
        "WHERE m.merge_id = %s::uuid AND m.user_id = %s "
        "  AND EXISTS (SELECT 1 FROM entities e WHERE e.entity_id = (x->>'a')::uuid) "
        "  AND EXISTS (SELECT 1 FROM entities e WHERE e.entity_id = (x->>'b')::uuid) "
        "ON CONFLICT DO NOTHING",
        (user_id, merge_id, user_id),
    )
    ms.execute(
        "INSERT INTO known_distinct_entity_pairs (user_id, entity_id_a, entity_id_b) "
        "VALUES (%s, LEAST(%s::uuid, %s::uuid), GREATEST(%s::uuid, %s::uuid)) "
        "ON CONFLICT DO NOTHING",
        (user_id, w, loser, w, loser),
    )

    # Name rules: restore the member's rules for that name only if nothing
    # changed them since the merge.
    rules = rec.get("name_rules")
    if rules:
        key = rules["name_norm"]
        now = _rules_snapshot(ms, user_id, key)

        def as_set(rows: list[dict]) -> set[tuple]:
            return {
                (r["from_type"], r["action"], r["to_type"], r.get("restore_to_type")) for r in rows
            }

        if as_set(now) == as_set(rules["after"]):
            ms.execute(
                # SCOPE-EXEMPT: entity_name_rules is per-member (no scope column).
                "DELETE FROM entity_name_rules WHERE user_id = %s AND name_norm = %s",
                (user_id, key),
            )
            for r in rules["before"]:
                ms.execute(
                    "INSERT INTO entity_name_rules "
                    "(user_id, name_norm, from_type, action, to_type, source_entity_id, "
                    "restore_to_type, display_name, document_evidence) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        user_id,
                        key,
                        r["from_type"],
                        r["action"],
                        r["to_type"],
                        r.get("source_entity_id"),
                        # Snapshots written before migration 084 carry none of these.
                        r.get("restore_to_type"),
                        r.get("display_name"),
                        list(r.get("document_evidence") or []),
                    ),
                )
        else:
            _log.info("entity_merge: undo left changed name rules alone merge_id=%s", merge_id)

    # Open records that followed the loser to the winner point back at the loser.
    ms.execute(
        "UPDATE entity_merges SET winner_id = %s::uuid "
        # SCOPE-EXEMPT: entity_merges is per-member (no scope column); user_id-bound.
        "WHERE user_id = %s AND merge_id = ANY(%s::uuid[]) AND winner_id = %s::uuid "
        "  AND undone_at IS NULL",
        (loser, user_id, list(rec["records_remapped"] or []), w),
    )
    ms.execute(
        "UPDATE entity_merges SET undone_at = NOW(), undone_by = %s WHERE merge_id = %s::uuid",
        (acted_by, merge_id),
    )

    restored = ms.fetch_one(
        # SCOPE-EXEMPT: the member's own restored row.
        "SELECT * FROM entities WHERE entity_id = %s::uuid AND user_id = %s",
        (loser, user_id),
    )
    survivor = ms.fetch_one(
        # SCOPE-EXEMPT: the member's own survivor row.
        "SELECT * FROM entities WHERE entity_id = %s::uuid AND user_id = %s",
        (w, user_id),
    )
    loser_scopes = [r["scope"] for r in ms.fetch_all(_PROJECTION_SCOPES_SQL, (loser, user_id))]
    winner_scopes = [r["scope"] for r in ms.fetch_all(_PROJECTION_SCOPES_SQL, (w, user_id))]
    for row in (restored, survivor):
        if row is not None:
            row["entity_id"] = str(row["entity_id"])
    if skipped:
        _log.info("entity_merge: undo merge_id=%s skipped_rows=%d", merge_id, skipped)
    return UndoOutcome(
        merge_id=merge_id,
        winner_id=w,
        loser_id=loser,
        user_id=user_id,
        restored_row=restored or {},
        survivor_row=survivor or {},
        merged_at=rec["merged_at"],
        moved_relation_ids=list(rec["relations_moved"] or []),
        restored_scopes=loser_scopes,
        winner_scopes_to_clear=[s for s in loser_scopes if s not in winner_scopes],
        skipped_rows=max(0, int(skipped)),
    )


def after_undo_commit(outcome: UndoOutcome) -> None:
    """Refresh search points and shared copies after an undo. Never raises."""
    from auth import UserContext
    from services.projection import projection_point_id

    from services import entity_edit

    user = UserContext(user_id=outcome.user_id, is_authenticated=True)
    try:
        vs = config.get_vector_store()
        for scope in outcome.winner_scopes_to_clear:
            try:
                vs.delete("entities", projection_point_id("entities", outcome.winner_id, scope))
            except Exception:  # noqa: BLE001
                _log.exception(
                    "entity_merge: failed to delete survivor shared point winner=%s scope=%s",
                    outcome.winner_id,
                    scope,
                )
    except Exception:  # noqa: BLE001
        _log.exception("entity_merge: vector store unavailable after undo")
    for row in (outcome.restored_row, outcome.survivor_row):
        if not row:
            continue
        try:
            entity_edit._refresh_search_point(row)
            entity_edit._refresh_shared_projections(row, user)
        except Exception:  # noqa: BLE001
            _log.exception("entity_merge: post-undo refresh failed entity=%s", row.get("entity_id"))
