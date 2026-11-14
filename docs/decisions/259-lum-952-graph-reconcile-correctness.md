# ADR-259: Graph reconcile correctness — orphan GC scope, idempotent co-occurrence, one stop list (LUM-952)

**Status:** Finalised
**Created:** 2026-11-11
**Last updated:** 2026-11-12
**Decided by:** /explore LUM-952 + Thomas (2026-11-11), /review-plan R1
**Finalised by:** /verify-plan 2026-11-12 (claude-opus-5-5)
**Plan:** `.cursor/plans/LUM-952-graph-reconcile-correctness.plan.md`
**Draft mirror:** `.cursor/adrs/LUM-952-graph-reconcile-correctness.md`
**Exploration:** `.cursor/explorations/LUM-952-graph-reconcile-correctness.md`
**Linear:** LUM-952 (absorbs LUM-953, LUM-956)

## Context

The graph service's daily reconcile has three defects found by LUM-54's live tests:
- The orphan GC scans only `:Entity` nodes, so personal merge losers are never collected and keep personal data that a merge should have removed.
- `RELATES_TO.co_occurrence_count` is incremented on every projection, so replays and two-sided projection inflate it.
- The graph service carries a stop-entities list, loader and admin routes that nothing uses. It has no entity extraction; Core filters stop phrases before it stores entities, in every graph mode.

## Decision

1. **GC scope.** The GC scans and deletes nodes carrying `:Entity` or any label in `NodeLabel.ENTITY_TYPE_MAP` (today `Person`, `Organisation`, `Project`, `Concept`), keyed on `(user_id, lumogis_id)` against the `entities` table. It never uses a label-agnostic match: evidence, code-structure and Memory nodes also carry `lumogis_id` and no `scope`.
   - It scans FalkorDB **before** reading Postgres, so an entity projected during the pass is never mistaken for an orphan.
   - It refuses to delete **personal** orphans when there are too many (above 500, or above 20% from 25 upward), unless forced. Shared and system `:Entity` orphans are deleted as before, because unshare and purge teardown depend on it.
   - It refuses **every** delete when the `entities` read returns no rows but the graph scan found entity nodes (wrong database or truncated read), unless forced. The shared-teardown exemption from the ratio guard does not extend to this case.
   - Deletes go by verified internal node id, in batches of 200. `deleted_ok` counts what FalkorDB reports it deleted, so a node that vanished since the scan is counted as failed, not deleted.
   - It has a dry-run mode that counts orphans per label without deleting.
2. **Co-occurrence.** `co_occurrence_count` is set to the absolute number of distinct evidence items both entities share in `entity_relations`, recomputed from Postgres on projection. It is no longer incremented. The projection write is **monotone**: a lower snapshot never overwrites a higher count, because the graph service projects on four workers. Replays are idempotent. Inflated edges are lowered only by the recount (`POST /kg/recount-cooccurrence`), which also covers existing graphs and leaves typed, count-less `RELATES_TO` edges alone. Reconcile computes the count once per stale entity, and skips it for an entity with no relations. Migration 081 indexes `entity_relations (evidence_id, user_id)`. Under the pair cap, the strongest pairs are kept, deterministically (`ORDER BY shared DESC`).
   - **Temporal interplay (LUM-104):** on the once-per-entity reconcile path there is no single evidence item, so the R1 replay guard's `ev_ts` is the pair's latest shared-evidence time (`max(least(a.created_at, b.created_at))`). It is never "now", which would revive every invalidated edge on every pass.
   - **Recount scope:** personal edges are those whose endpoints have no `scope` or `scope = 'personal'`. Today's writer leaves `scope` unset; LUM-961 (draft ADR) will stamp `'personal'`, and the recount accepts both.
3. **Stop list.** Core owns the only stop list (`{workspace}/config/stop_entities.txt`, LUM-922), because Core is the only consumer: it discards stop phrases before entities reach Postgres and Qdrant. The graph service's copy is removed. Its loader and its uncalled `quality/entity_quality.py` are deleted, and its `GET`/`POST /kg/stop-entities` return 410 with a pointer to Core's `/graph/mgm`.
4. **Thresholds.** The mass-delete guard refuses above 500 orphans, or above 20% of the scanned entity nodes once there are at least 25. `GRAPH_COOCCURRENCE_THRESHOLD` stays 3.

## Alternatives Considered

- **Label-agnostic GC match** (`scope IS NULL`): rejected. It would delete evidence and code-graph nodes.
- **`entity_type IS NOT NULL` discriminator:** not chosen. It rests on an implicit property contract.
- **Per-edge evidence-id set:** not chosen. It grows without bound, duplicates `entity_relations`, and does not heal existing counts.
- **Increment only on a new `MENTIONS` edge:** not chosen. It does not fix the two-sided double count.
- **Mount Core's list into the graph service (read-write or read-only), have it fetch the list from Core, or persist its own copy:** rejected. All three assumed the graph service filters with the list, and it does not.

## Consequences

- Personal merge losers and deleted entities disappear from the graph within one daily cycle.
- A wrong or empty live set can no longer wipe the graph without an explicit override.
- `co_occurrence_count` means "distinct shared evidence". Values drop where they were inflated, so fewer edges pass `GRAPH_COOCCURRENCE_THRESHOLD` (default 3) until the threshold is re-tuned.
- LUM-569 and LUM-369 must use the absolute-count semantics.
- The GC matches on labels only, never on `scope`, so it is unaffected when LUM-961 stamps `scope = 'personal'` on personal nodes.
- A real household graph needs one dry run (`POST /kg/orphan-gc`, the default) before the widened GC's first destructive daily pass reaches private `main`. The dev stack runs with graph mode off, so this could not be done during verification.
- There is one stop list, edited only through Core. The graph service's legacy page is unchanged. Its existing error display now shows "Failed to load stop entity list: The stop-entity list is managed by Lumogis Core (/graph/mgm) …" instead of editing a file that has no effect.

## Revisit conditions

- A new node kind with entity semantics is added outside `ENTITY_TYPE_MAP`.
- Projection volume makes the per-call aggregate query a measurable cost; at that point, batch it with LUM-569.
- The graph service gains its own entity extraction. It would then need the list, and should read Core's rather than keep a copy.

## Status history

- 2026-11-12: Finalised by /verify-plan. Implementation added the empty-live-set refusal, honest delete counts, a deterministic pair-cap order, the reconcile-path temporal timestamp, the stub-entity skip, and recount scope tolerance for LUM-961. All of these came from code review or verification, within the decision above.

- 2026-11-11: Draft by /explore LUM-952 (claude-opus-5-5).
- 2026-11-11: Revised at /review-plan --arbitrate R1. Projection writes are monotone, with the recount as the only lowering path; the guard covers personal labels only; deletes are batched by id; the evidence index migration was added; the page wording was made accurate.
- 2026-11-11: Revised after Thomas's questions. The stop-list decision changed from "share via a mount" to "remove the dead copy", and the guard thresholds and threshold choice were recorded.
