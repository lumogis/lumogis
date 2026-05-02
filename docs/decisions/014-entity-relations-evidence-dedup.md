# ADR: `entity_relations` evidence dedup

**Status:** Finalised
**Created:** 2026-04-19
**Last updated:** 2026-04-19 (finalised by `/verify-plan` — implementation confirmed Option 1 decision; no deviations)
**Finalised copy:** `docs/decisions/014-entity-relations-evidence-dedup.md`
**Decided by:** composer-2 (Opus 4.7), via `/explore entity_relations_evidence_dedup`

## Context

`orchestrator/services/entities.py:328-333` writes `entity_relations` rows with a plain `INSERT` (no `ON CONFLICT`). Re-extracting entities from the same evidence — re-ingesting the same file, re-summarising the same session, the reconcile/replay path — appends duplicate `(source_id, evidence_id, relation_type, user_id)` rows. Today's correctness damage is bounded because every cooccurrence-math reader (`services/edge_quality.py`, the `services/lumogis-graph/` mirror, `routes/admin.py:1530`, `services/lumogis-graph/graph/writer.py:535`) already uses `COUNT(DISTINCT evidence_id)` or `SELECT DISTINCT source_id`. But three readers — `services/tools.py:109-112` (MCP `entity.lookup` recent-relations panel, user-visible), `routes/admin.py:2497-2502` (per-user JSONL data export, user-visible), `services/lumogis-graph/graph/reconcile.py:172-175` (replay work amplification) — leak duplicates into output, and storage bloats linearly with re-ingest frequency.

This was carved out as the soft-guard exit from `per_user_file_index_and_ingest_attribution` (2026-04-19) because the right fix needs a real migration plus a deliberate semantic decision (first-observed vs last-observed), not the ≤ 5-line in-flight fold-in the soft-guard ceiling allowed.

The constraints that shaped the option space:
- **Greenfield reality** — no existing real `entity_relations` content of consequence; we ship the intended end-state directly.
- **Multi-user safety is already correct** — every reader/writer filters `WHERE user_id = %s`. This is purely a within-user dedup, NOT a B11/B12-shaped silent-leak risk.
- **Project pattern alignment** — `file_index ON CONFLICT (user_id, file_path)` (just shipped), `sessions ON CONFLICT (session_id)`, `users PRIMARY KEY` — every other Postgres write site in the project enforces uniqueness at write time. `entity_relations` is the only outlier.
- **Append-only event-log convention** — the canonical 2026 Postgres pattern for evidence/observation tables is `INSERT … ON CONFLICT DO NOTHING` with first-observed `created_at` semantics. Validated against contemporary Postgres guidance.

## Decision

Add `UNIQUE(source_id, evidence_id, relation_type, user_id)` to `entity_relations` and change the writer at `services/entities.py:328-333` to `INSERT … ON CONFLICT (source_id, evidence_id, relation_type, user_id) DO NOTHING`. `created_at` retains its first-observed semantics. Greenfield: no pre-cleanup, no backfill — direct `ADD CONSTRAINT` migration. As a defence-in-depth side effect, add `DISTINCT ON (evidence_id, relation_type)` at the user-visible read site `services/tools.py:109-112` so the MCP `entity.lookup` recent-relations panel stays clean even if a future code path bypasses the constraint.

**Locked-in scope (2026-04-19, by user before `/create-plan`):**
1. **UNIQUE tuple** — exactly `(source_id, evidence_id, relation_type, user_id)`. `evidence_granularity` is NOT in the tuple (granularity is a property of the link, not a key dimension; future paragraph-level extractor → sibling overrides table, not relaxed UNIQUE).
2. **`tools.py:109-112` `DISTINCT ON` defence-in-depth** — folded into this chunk (2-line change, directly user-visible via MCP `entity.lookup`).
3. **Test placement** — extend `orchestrator/tests/test_entities.py` with one new test `test_store_entities_is_idempotent_on_repeat_evidence`. No new test module.
4. **`services/lumogis-graph/quality/edge_quality.py` mirror** — no code change (math is already DISTINCT-protected); one-line comment noting the upstream dedup contract is folded into this chunk for cross-repo discoverability.
5. **Greenfield only — no backfill** in this chunk. The one-time `DELETE … USING ROW_NUMBER` cleanup query is preserved as a non-blocking exploration note for whoever first deploys against a real install carrying duplicates.
6. **Restore path (`routes/admin.py:2384-2385`)** — no code change. The existing generic `ON CONFLICT DO NOTHING` (no inference column list) at `INSERT INTO {table} ({col_list}) VALUES ({placeholders}) ON CONFLICT DO NOTHING` is already forward-compatible with the new UNIQUE — Postgres treats no-inference-target `ON CONFLICT DO NOTHING` as "no-op on any unique-constraint violation." Gated by a **source-grep regression gate** in `orchestrator/tests/test_entities.py` (Test 4 — `inspect.getsource(routes.admin)` regex pinning that the restore SQL stays constraint-agnostic, with a forbidden-shape check rejecting any `ON CONFLICT (…)` inference target sneaking in). Revised by `/review-plan --arbitrate R1` from the original "backup-restore round-trip integration test" framing — the source-grep contract is what we actually want to gate (constraint-agnosticism, surviving any new UNIQUE), not behavioural restore round-trips that would require fixture-heavy setup for marginal additional coverage.
7. **Migration numbering** — `postgres/migrations/012-entity-relations-evidence-uniq.sql` (revised from `014-…` by `/review-plan --arbitrate R1`; latest existing migration verified as `011-per-user-file-index.sql`, so `012` is the actual next slot). At PR-write time the implementer MUST re-verify slot availability and bump to the next free slot if `012` has been taken by a concurrent chunk (`personal_shared_system_memory_scopes` has provisionally claimed `012-memory-scopes.sql`; `per_user_connector_credentials` provisionally `013-…`; `mcp_token_user_map` provisionally `014-…`). Lexical migration ordering is the only thing that matters; sequencing against any other chunk is independent.
8. **Sequencing against `personal_shared_system_memory_scopes`** — independent; whichever ships first wins the migration slot.

## Alternatives Considered

- **Option 2 — `ON CONFLICT DO UPDATE SET created_at = NOW()`** — works mechanically, but creates inconsistent timestamp semantics between the writer path and the `entity_merge.py` re-pointing path (merge updates `source_id` without bumping `created_at`); contradicts the 2026 append-only event-log convention; no current reader benefits from "last observed" over "first observed".
- **Option 3 — DISTINCT-on-read at every reader** — leaves storage bloat unbounded; weakens the invariant from "table-enforced" to "every-reader-must-remember"; conflicts with the project's own pattern (every other Postgres write site enforces uniqueness at write time).
- **Option 4 — Periodic batch dedup job** — strictly worse than write-time dedup on every axis (correctness window, latency, operational complexity).
- **Option 5 — Pre-check then INSERT** — the retired race-prone pattern that `per_user_file_index_and_ingest_attribution` just removed from `services/ingest.py`. Listed for completeness only.

See *(maintainer-local only; not part of the tracked repository)* for full details, including which downstream readers are already DISTINCT-protected, which leak duplicates today, and the two-line `services/tools.py` defence-in-depth fix.

## Consequences

**Easier:**
- `entity_relations` becomes a true append-only fact table — every row encodes one durable claim "entity X was first observed in evidence Y at time T".
- The MCP `entity.lookup` "recent relations" panel stops being dominated by re-ingest duplicates of the same evidence.
- The per-user JSONL data export stops over-reporting evidence row counts.
- Graph reconcile/replay (`services/lumogis-graph/graph/reconcile.py:172-175`) does N×fewer redundant MERGE calls per stale entity.
- Storage growth becomes proportional to *distinct evidence observations*, not to re-ingest frequency.
- The codebase has one consistent ON CONFLICT idiom across all write sites (`file_index`, `entity_relations`, future tables).

**Harder / closed off:**
- Storing per-re-observation metadata (e.g. "this entity was re-confirmed by 5 different ingest runs") now requires a sibling table (e.g. `entity_observations`) — not a relaxation of this UNIQUE. Acceptable: no current product surface needs it.
- "Last observed" timestamp semantics are foreclosed without a follow-up rework.
- A future paragraph-level extractor that emits non-`'document'` `evidence_granularity` for an evidence_id that already has a 'document'-level row will be silently dropped at write time. Acceptable: granularity overrides are a future concern, and the right answer if it ever lands is a sibling overrides table (see exploration).
- Restore from a pre-dedup backup must wire the restore-path INSERT through `ON CONFLICT DO NOTHING` (or a one-time pre-migration cleanup), or it will fail with `duplicate key violation`. Acceptable: greenfield, no real backups in circulation; the restore-path change is folded into the same chunk if trivial, otherwise named as the `restore_path_idempotent_inserts` follow-up.

**What future chunks must know:**
- `entity_relations` is now constraint-enforced unique on `(source_id, evidence_id, relation_type, user_id)`. New writers MUST use `INSERT … ON CONFLICT (…) DO NOTHING` or they will raise on duplicate key. New readers MAY drop defensive DISTINCT, but should keep it where the surface is user-visible (defence in depth).
- `created_at` on `entity_relations` rows is "first observed", not "last observed".
- `personal_shared_system_memory_scopes` (planned `012-…`) adds a `scope` column to this table and is unaffected by this UNIQUE — neither chunk blocks the other.

## Revisit conditions

- **If a future product surface genuinely needs per-re-observation metadata** (e.g. an "evidence trail timeline" UI listing every distinct ingest run that confirmed the same connection), the right move is a new `entity_observations` sibling table — NOT relaxing this UNIQUE. Revisit the dedup ADR only if that sibling-table approach is somehow infeasible.
- **If a paragraph-level / sentence-level extractor lands** that meaningfully wants to stamp granularity per-link rather than per-row, revisit whether granularity belongs in the UNIQUE tuple (recommendation today: no, it belongs in a granularity-overrides sibling).
- **If Postgres ever deprecates `ON CONFLICT DO NOTHING`** in favour of standard SQL `MERGE` and the project consolidates all upserts to `MERGE`, revisit the SQL surface (no semantic change).

## Status history

- 2026-04-19: Draft created by `/explore entity_relations_evidence_dedup`.
- 2026-04-19: All 8 open questions locked-in by user before `/create-plan`. Headline new lock: restore-path requires NO code change (verified `routes/admin.py:2384-2385` already emits generic `ON CONFLICT DO NOTHING` with no inference column list, which is forward-compatible with any new UNIQUE). Previously-named follow-up `restore_path_idempotent_inserts` is closed and removed. Folded into this chunk: `tools.py:109-112` `DISTINCT ON` defence-in-depth, `services/lumogis-graph/quality/edge_quality.py` mirror comment, backup-restore round-trip assertion in the new integration test.
- 2026-04-19: Revised during `/review-plan --arbitrate R1` (composer-2, Opus 4.7) to reconcile two stale items with the plan after self-review and Round 1 critique: **(a)** locked-in scope item 7 migration filename `014-…` → `012-…` (latest existing migration verified as `011-per-user-file-index.sql`; the original `014` was based on planned-but-unwritten `012-memory-scopes.sql` and `013-…` that have not happened); **(b)** locked-in scope item 6 verification shape changed from "backup-restore round-trip integration test" to "source-grep regression gate in `orchestrator/tests/test_entities.py` Test 4" because the source-grep contract correctly gates the actual invariant (the restore SQL stays constraint-agnostic, surviving any new UNIQUE), not behavioural restore round-trips. Architectural decision (Option 1, UNIQUE + `ON CONFLICT DO NOTHING`, first-observed semantics, greenfield) is unchanged. ADR status flipped from "Draft" to "Draft (revised)".
- 2026-04-19: Finalised by `/verify-plan` (composer-2, Opus 4.7) — implementation confirmed every locked-in scope item 1–8 with zero architectural deviations. Concrete confirmations against shipped code: writer at `orchestrator/services/entities.py:328-334` emits `INSERT … ON CONFLICT (source_id, evidence_id, relation_type, user_id) DO NOTHING`; `postgres/migrations/012-entity-relations-evidence-uniq.sql` ships the UNIQUE INDEX as planned (no pre-cleanup); `postgres/init.sql` mirrors the index for fresh installs; `orchestrator/services/tools.py:127-138` wraps `DISTINCT ON (evidence_id, relation_type)` in a subquery with outer `ORDER BY created_at DESC LIMIT 10` (preserves "10 most recent" semantics post-DISTINCT-ON, with explicit upstream-user-scoping documentation block); `orchestrator/services/entity_merge.py:_run_phase_a` lines 87-108 contain TWO consecutive `with conn.cursor()` blocks (UPDATE-WHERE-NOT-EXISTS at 87-100, then DELETE at 102-108), both inside the parent transaction; `_run_phase_a` returns `relations_dropped_as_duplicate` (line 156) and `merge_entities` log line at 274 surfaces it (log-only, `MergeResult` model unchanged); restore path at `orchestrator/routes/admin.py:2384-2385` confirmed unchanged (generic `ON CONFLICT DO NOTHING` shape preserved, gated by `tests/test_entities.py::test_restore_path_remains_generic_on_conflict_do_nothing`); `orchestrator/services/edge_quality.py` and `services/lumogis-graph/quality/edge_quality.py` docstrings note migration-012 contract. All 5 plan tests pass (4 in `test_entities.py` + 1 in `test_entity_merge.py`); full orchestrator suite: 746 passed, 4 pre-existing failures unrelated to this chunk (`test_capability_health.py` × 2 owned by `capability_launchers_and_gateway`, `test_graph_reconcile.py::TestBackfillEndpoint` × 2 owned by `lumogis_graph_service_extraction`). Status flipped from "Draft (revised)" to "Finalised". Finalised copy written to `docs/decisions/014-entity-relations-evidence-dedup.md`.
