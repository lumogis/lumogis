# ADR-234: An entity merge repoints active typed edges to the winner (LUM-598)
**Status:** Finalised
**Created:** 2026-11-07
**Last updated:** 2026-11-07
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-11-07 (claude-opus-5-5)
**Plan:** none — shipped before formal plan / verify cycle for this chunk
**Exploration:** `.cursor/explorations/entity_merge_edge_repoint_retro.md`
**Draft mirror:** `.cursor/adrs/entity_merge_edge_repoint.md`
**Linear:** LUM-598

## Context

`entity_merge` re-projected nodes and garbage-collected the loser, but never moved `entity_edges.src_entity_id` / `dst_entity_id`. Typed edges that named the loser matched nothing after the merge, and the LUM-580 reconcile re-tried them forever.

## Decision

Inside the merge transaction, before the loser is deleted, `_repoint_entity_edges` (`orchestrator/services/entity_merge.py`) runs four statements, each scoped to the user, across all banks, on **active** edges only (`valid_until IS NULL`):

1. Edges that would become self-loops (loser→loser, loser→winner, winner→loser) are soft-archived with `valid_until = now()`.
2. A loser edge whose repointed key `(user_id, bank, src, dst, relation_type)` already exists as an active winner edge is deleted. The winner's row wins.
3. The remaining endpoints move to the winner with `graph_projected_at = NULL`, so the reconcile re-attaches them. `id`, `evidence_id`, `valid_from`, `valid_until` and `created_at` never change.
4. An active loser edge that collides with an **archived** winner edge is left as it is, counted (`edges_unmerged`) and logged.

### As-implemented surface

Phase A result keys: `edges_repointed`, `edges_dropped_as_duplicate`, `edges_selfloop_archived`, `edges_unmerged`. `MergeResult` is unchanged.

### Pre-existing defects fixed with it

The first live run showed `merge_entities` failing on every real Postgres before this change: Step 3 cast to `uuid` against `sessions.entity_ids TEXT[]`, and `remap_published_from` swept `file_index.published_from INTEGER` with an entity UUID. Step 3 now compares as text, and the merge remaps only `entities` (`remap_published_from(..., tables=("entities",))`; each projection table's `published_from` references its own key).

## Alternatives considered

- **Hard-delete self-loops.** Not chosen: edges are retired, never destroyed (ADR 129, ADR 151).
- **Repoint into a self-loop.** Not chosen: `entity_constraints` treats `no_self_loop` as CRITICAL.
- **Resolve archived-slot collisions in the merge** (revive the archived winner edge, or drop the live loser edge). Not chosen: that is a temporal-validity rule (ADR 133 / 151 R1), not a merge mechanic.
- **Move archived edges too.** Not chosen: they are history and excluded from reconcile.

## Consequences

- Typed edges follow the survivor of a merge and are re-projected on the next reconcile.
- A merge can leave a small, logged set of active edges that still name the deleted entity.

## Revisit conditions

- `edges_unmerged` appears in real logs, or the temporal contract (LUM-528) settles archived-slot collisions.
- The UNIQUE index on `entity_edges` becomes active-rows-only: step 4 goes away.
- LUM-599 adds reconcile attempt counting.

## Linear linkage (Product OS)

- **Existing issue:** LUM-598
- **New issue needed:** no
- **Historical evidence only:** no

## Testing retrospective

11 unit cases pin the statement shapes; 6 live Postgres/FalkorDB cases (`tests/integration/test_entity_merge_edges_live.py`) prove behaviour, including merge then reconcile. Live: 7 passed (with the existing reconcile live test) after the two pre-existing fixes; the first run failed 5/5 on them. Full unit suite: no new failures against `dev`.

## Status history

- 2026-11-07: Finalised by /record-retro (retrospective).
