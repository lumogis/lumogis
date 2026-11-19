# ADR-270: Undoable entry merge with a per-merge change record (LUM-965)
**Status:** Finalised
**Created:** 2026-11-16
**Last updated:** 2026-11-17
**Decided by:** claude-opus-5-5 (`/explore --headless LUM-965`); revised at /review-plan arbitration R1
**Finalised by:** /verify-plan 2026-11-17 (claude-opus-5-5)
**Plan:** `.cursor/plans/LUM-965-entity-merge-undo.plan.md`
**Exploration:** `.cursor/explorations/LUM-965-entity_merge_undo.md`
**Draft mirror:** `.cursor/adrs/LUM-965-entity_merge_undo.md`
**Linear:** LUM-965
**Related:** [ADR 234](234-lum-598-entity-merge-edge-repoint.md) (the merge engine and typed-edge repoint), [ADR 266](266-lum-161-entity-browser.md) (the `entity_edit.py` member write path and its advisory lock), [ADR 269](269-lum-967-entity-kind-correction.md) (per-member name rules), [ADR 158](158-graph-aware-entity-sharing-lum586.md) (shared entity projections), [ADR 259](259-lum-952-graph-reconcile-correctness.md) (monotone co-occurrence rule)

## Context

Members need to merge two of their own entries on the "What Lumogis knows" page and undo the merge later. Before this change the engine, `services/entity_merge.py` `merge_entities()` (ADR 234), was admin-only, deleted the loser and kept no history. The automatic duplicate finder (`services/deduplication.py`) called the same engine silently, so its mistakes were permanent too. Undo has to work after later edits, later merges, re-ingest and graph projection have moved on. It must also respect member ownership (ADR 266), shared projections (`published_from`, ADR 158) and the per-member name rules (ADR 269). The KG is AGPL core (ADR 229), and no new service or dependency is wanted.

## Decision

Every merge writes one row to a new table, `entity_merges` (migration 083, also in `init.sql`), in the same Postgres transaction as the merge. This covers merges by the member, the review queue, an admin and automatic dedup. The row records by id what the merge changed:

- the loser's full row;
- the winner's previous name, other names, tags and count;
- the ids of the moved `entity_relations`, `entity_edges` and projection rows;
- the full rows the merge deleted;
- the session arrays it rewrote;
- the distinct pairs it carried over or lost;
- any retype rule it created;
- the older records it re-pointed.

Each statement records its own ids ("record-in-place": `WITH affected AS (… RETURNING id) UPDATE entity_merges SET col = …`), so ids never leave Postgres. The engine has two halves. `merge_in_transaction` runs inside the caller's transaction. `after_merge_commit` fires `ENTITY_MERGED` and refreshes Qdrant, best-effort. `merge_entities` wraps both. Every origin takes the member advisory lock from ADR 266, so dedup and admin merges serialise with member edits.

Undo replays the record in reverse in one transaction, under the same lock. **Undo is newest first per survivor.** A merge can be undone only when no newer open merge into the same survivor exists. A chain re-points older records to the new survivor, so the rule covers chains too. Undoing the newest merge is exact by construction:

- It touches only recorded rows that are still where the merge left them. Every replay statement is guarded, and rows that cannot go back are counted in `skipped_rows`.
- It re-inserts dropped rows verbatim. A dropped relation comes back only while its evidence is still live, so a purged document never returns.
- It forces `user_id` from the record, never from the JSONB snapshot.
- It keeps whatever the survivor gained since, and lists the mentions added after the merge so the member can move them (`move_mentions`, by `entity_relations.id`).

An undone pair is written to the existing `known_distinct_entity_pairs` table, which both dedup copies already honour.

The engine merges only personal source rows, whatever the origin. Merges that the member does not make on the entry page (automatic dedup, review queue, admin) never change what the household sees. They are refused with `MergeChangesSharing` when the two entries' shared scopes differ: dedup queues the pair, and the review queue and the admin route answer `409 sharing_differs`. The admin route's `user_id` is now required. A lock wait over 5 s leaves a dedup pair for the next run, and the review-queue and admin routes answer `503 entry_busy`.

The graph follows from Postgres, and no new event is added. For a stale entity named in an `entity_merges` row, `reconcile_entities` replays as before and **then** prunes the edges Postgres does not back:

- MENTIONS that are not in the replay list;
- DISCUSSED_IN to sessions whose non-empty `entity_ids` lack the entity (sessions with empty `entity_ids` are resolved by name and left alone);
- typed entity edges with no active `entity_edges` row in the default bank;
- zero-share co-occurrence edges.

The prune is label-scoped, and a failed prune unstamps the row so the next pass retries. The shared Qdrant point's `payload.entity_id` is now the projection row's own id, so a projection moved by a merge still resolves in household chat context. Undo records are kept out of the per-user export and the admin JSON backup, because import renumbers the relation ids they name.

## Alternatives Considered

See `.cursor/explorations/LUM-965-entity_merge_undo.md` for full detail.

- **Full before-snapshot of both entries:** undo would overwrite everything the survivor learned after the merge.
- **Trigger-based history tables:** every write to four busy tables would be recorded to support one rare action.
- **Undo in any order:** rejected at plan review (arbitration R1). It needed per-chain routing of dropped rows, a fix-up of restored snapshots and projection id-matching. Even then, two independent merges into one survivor could lose a shared document's mention. The record holds what it would need, so it remains possible later work.
- **Global LIFO across all entries:** stricter than needed; per-survivor LIFO is enough for exactness.
- **Undo by splitting on evidence:** this cannot restore the loser's id, other names, typed edges or shared copies.
- **A new `ENTITY_UNMERGED` event (G1):** it adds a webhook model on both sides, and the graph is still wrong whenever the webhook is lost. At plan time typed edges turned out to MERGE on their endpoints with no id. That was this alternative's revisit condition, and the typed-edge prune resolved it instead.
- **Narrowest scope wins on merge:** the household would lose sight of an entry without being told. Member merges keep the survivor's sharing and the dialog says what becomes visible. Non-member merges are refused when sharing differs.
- **A new "not the same" table:** it duplicates `known_distinct_entity_pairs`.
- **Soft-deleting the loser as a redirect:** every read path would need an "is merged" filter.

## Consequences

- **Easier:**
  - Every merge can be undone, including automatic ones, newest first per entry.
  - Split (LUM-968) reuses `known_distinct_entity_pairs` and `move_mentions_in_transaction`.
  - The graph heals stale MENTIONS edges of merge-involved entries after a lost webhook.
- **Harder:**
  - Every write path that deletes an entry by merging must keep merge records consistent: a later merge re-points the open records of the entry it deletes, and undo points them back.
  - Delete (LUM-966) must remove the records that hold a deleted entry's data. The FK on `winner_id` cascades.
  - A later migration that adds a NOT NULL column without a default to `entities` must backfill `entity_merges.loser_row` (note in migration 083).
- **Impossible:** merges made before migration 083 have no record and cannot be undone. Undo records do not travel with a per-user export.
- **Residuals (disclosed in the guide and in code):**
  - The household (shared) graph is not pruned after undo.
  - A legacy session's name-resolved edge stays on the survivor.
  - A mention written by an extraction already running at merge time can miss the "added since" list.
  - Inflated non-zero co-occurrence counts stay until `recount_cooccurrence` (ADR 259).
  - The loser's derived rows (`review_queue`, `constraint_violations`, `edge_scores`, `dedup_candidates`) are not restored; the next quality and dedup runs recompute them.
  - The merge picker does not filter out staged entries (the search card has no `is_staged`); the server refuses them with `staged_entry`.
- **Lag:** after an undo, the graph lags until the next reconcile pass. The page, search and chat are right immediately.

## Revisit conditions

- Members ask to undo an older merge without undoing newer ones → add out-of-order undo on the same record (Later in LUM-965).
- The typed-edge prune proves unreliable on FalkorDB (F0/F2 fail, or edges reappear) → add the `ENTITY_UNMERGED` event for typed edges only.
- A split or other writer that moves mentions without a merge record needs the graph to follow → widen the prune gate (LUM-968).
- Reconcile moves off its daily cadence, or members report graph lag after undo → add an immediate per-entity re-project.
- `entity_merges` grows past about 10k rows per member, or LUM-966 sets a retention rule for personal data → add expiry of undo records.
- A MENTIONS edge writer without an `entity_relations` row appears → reconsider pruning in reconcile.

## Implementation notes

- Engine: `orchestrator/services/entity_merge.py` (`merge_in_transaction`, `after_merge_commit`, `merge_entities`, `undo_in_transaction`, `after_undo_commit`, `move_mentions_in_transaction`).
- Member path: `orchestrator/services/entity_edit.py` (`merge_entries`, `undo_merge`, `list_merge_history`, `move_mentions`); routes in `orchestrator/routes/api_v1/kg.py`.
- Graph: `services/lumogis-graph/graph/reconcile.py` (`_prune_merged_entity`, gated by `_entity_is_merge_involved`); `graph/writer.py` `_cooccurrence_siblings`.
- Proof: `orchestrator/tests/integration/test_entity_merge_undo_live.py` (L1–L23, including a randomised model test), `test_graph_merge_undo_falkordb_live.py` (F0–F4, live FalkorDB), `services/lumogis-graph/tests/test_reconcile_prune.py` (G1–G7).

## Status history
- 2026-11-16: Draft created by /explore
- 2026-11-16: Revised during /review-plan --arbitrate R1: undo became newest first per survivor, a typed-edge prune overrode the G1 revisit condition, the prune is gated and runs after replay, non-member merges may no longer change household visibility, and undo records are omitted from export
- 2026-11-17: Finalised by /verify-plan. The implementation confirmed the decision. Additions at verification: the review-queue and admin routes answer `503 entry_busy` on a lock wait, undo counts shared copies it could not put back in `skipped_rows`, and the residual that the picker does not filter staged entries is recorded
