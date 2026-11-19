# ADR-264: Graph-service deduplication keeps the personal-scope ANN filter (LUM-938, cycle 1)

**Status:** Finalised
**Created:** 2026-11-16
**Last updated:** 2026-11-16
**Decided by:** claude-opus-5-5 (`/explore --headless LUM-938`); cycle-1 scope set by Thomas (2026-11-16, `/portfolio sprint`)
**Finalised by:** /verify-plan 2026-11-16 (claude-opus-5-5)
**Plan:** `.cursor/plans/LUM-938-graph-dedup-scope-filter.plan.md`
**Exploration:** `.cursor/explorations/LUM-938-graph_dedup_scope_filter.md`
**Draft mirror:** `.cursor/adrs/LUM-938-graph_dedup_scope_filter.md`
**Related:** [ADR 015](015-personal-shared-system-memory-scopes.md) (personal / shared / system scopes), [ADR 158](158-graph-aware-entity-sharing-lum586.md) (shared entity projections), [ADR 138](138-lum-293-bank-isolation.md) (Qdrant payload tenancy), [ADR 229](229-lum-855-kg-agpl-relicence.md) and [ADR 239](239-lum-855-kg-publication-as-built.md) (the Knowledge Graph is AGPL and public), [ADR 259](259-lum-952-graph-reconcile-correctness.md) (deleted the graph copy of `entity_quality.py`)

## Context

Weekly entity deduplication finds candidate pairs partly through a vector search (ANN) over Qdrant's `entities` collection. Core's copy (`orchestrator/services/deduplication.py`, `_build_ann_blocks`) filters that search on `user_id = <member>` **and** `scope = 'personal'`, so a member's personal entity is never paired with a shared or system projection of it (ADR 015, ADR 158). The graph service's copy (`services/lumogis-graph/quality/deduplication.py`) kept only the `user_id` clause. That defect is FU-1 §2.2b from the LUM-855 relicence.

It was inert for two reasons. First, the `entity_map` guard only accepts neighbours from the personal-only entity list the job loads. Second, the graph copy's ANN branch cannot run in any shipped mode: the graph tree has no Qdrant or embedder adapter, so `config.get_vector_store()` / `get_embedder()` return `None` and the guard skips ANN; `GRAPH_MODE=inprocess` runs Core's copy. No test on either side checked the scope clause, and Core's `TestAnnUserIdFilter` passed even when no search ran. Thomas limited LUM-938 cycle 1 to this fix, with module convergence deferred (2026-11-16).

## Decision

The graph copy's ANN filter goes back to Core's two-clause form (`user_id` and `scope == "personal"`), with Core's explanatory comment in place of the KNOWN DRIFT note. The comment and `vs.search` block are byte-identical in both copies. Both clauses are part of the contract for every dedup ANN search, in whichever copy runs.

A graph-tree unit test (`services/lumogis-graph/tests/test_deduplication_ann_filter.py`, the first test of a `quality.*` module there) asserts both clauses on every `vs.search` call with a non-default member id, and a call count of exactly two. Two sibling tests pin the `None` embedder / vector-store guard. Core's `TestAnnUserIdFilter` gets the same scope assertion and a non-zero call count. Converging the modules is unchanged and stays in LUM-938's Later section.

## Alternatives Considered

- **A shared `_ann_filter()` helper in each copy:** it adds a symbol convergence would collapse, and the call-level test is still needed.
- **Full dedup convergence now (FU-1 §2.5):** this is the right end state, but it is out of cycle-1 scope and carries the container-import risk R2 (the Core image does not ship `services/lumogis-graph/`).
- **Delete the graph copy's ANN branch, since it is unreachable:** this contradicts FU-1's direction that the graph copy is canonical for dedup, and would drop ANN blocking from Core after convergence.
- **Rely on the `entity_map` guard:** privacy would hold only by accident, and it does not meet the issue's acceptance.

Full detail: `.cursor/explorations/LUM-938-graph_dedup_scope_filter.md`.

## Consequences

- **Easier:** the graph copy matches Core on the only behavioural difference FU-1 says to revert. Deduplication convergence can then move the file and its test without deciding anything new.
- **Harder:** nothing. A Qdrant point with no `scope` payload is excluded from ANN candidates, the same as Core does today.
- **No runtime change in any shipped mode.** The fix is defence in depth until the graph tree gains ANN adapters or the copies converge.
- **Future work must know:** whoever converges deduplication, or adds service-mode ANN adapters, keeps both clauses and the graph-tree test. A one-clause filter is a regression.
- **Not covered by a mock:** that Qdrant excludes a point with no `scope` key is server-side behaviour. The live test belongs with convergence, when the graph copy runs against a real Qdrant (LUM-938 Later).

## Implementation notes

- Verified 2026-11-16: the graph test fails (1 failed, 2 passed) with the scope clause removed and passes with it; Core's new scope test fails with Core's scope line removed; the `call_count == 2` check fails when every embed raises. `make test-kg` 334 passed / 7 skipped; `make test` 6809 passed / 320 skipped / 3 xfailed.

## Revisit conditions

- Entities gain a scope other than `personal` that dedup should legitimately compare, for example a member's own items inside a household space (LUM-932 / LUM-893). The clause then becomes `match: {any: [...]}` in both copies and both tests.
- Payload tenancy for `entities` is replaced by per-member collections or shards, which would make the `user_id` clause redundant (ADR 138).
- Deduplication moves off Qdrant ANN blocking altogether, for example to the MinHash/LSH resolution proposed in LUM-965.

## Status history

- 2026-11-16: Draft created by /explore
- 2026-11-16: Finalised by /verify-plan — implementation confirmed decision
