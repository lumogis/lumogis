# ADR-141: Codegraph reindex serialization — prevent cross-run sweep data loss (LUM-301)

**Status:** Finalised

**Created:** 2026-06-29

**Last updated:** 2026-06-29

**Decided by:** as-shipped implementation (retrospective)

**Finalised by:** /record-retro 2026-06-29 (Composer)

**Plan:** none — post-shipment hardening after ADR-134 verify-plan; discovered via Cursor critical-bug investigation

**Exploration:** `.cursor/explorations/lum_301_codegraph_reindex_serialization_retro.md`

**Draft mirror:** `.cursor/adrs/lum_301_codegraph_reindex_serialization.md`

**Linear:** [LUM-301](https://linear.app/lumogis/issue/LUM-301) (programme **Done**; this record documents a post-ADR-134 concurrency fix)

**Extends:** [ADR-134](134-lum-301-codegraph-code-structure-ingest.md) (code-structure ingest)

## Context

ADR-134 (LUM-301) shipped native tree-sitter code-structure ingest with a write-new-then-sweep-old pattern: each run gets a unique `ingest_run_id`, projects nodes/edges, then sweeps prior-run rows per file. That design is correct for single-threaded runs.

After merge to `dev`, Cursor investigation found that **overlapping** `ingest_roots` invocations — e.g. manual `POST /codegraph/reindex` while the APScheduler job runs, or two concurrent HTTP requests — could interleave as:

1. Run A projects with `ingest_run_id=RA`
2. Run B projects with `ingest_run_id=RB`
3. Run A's per-file sweep deletes nodes where `ingest_run_id <> RA`, including B's fresh rows for shared files

Data loss without any parse error. The race lives in the gap between projection and sweep across runs, not in tree-sitter parsing.

## Decision

Hold a **module-level `threading.Lock`** (`_INGEST_LOCK`) across `writer.project_code_structure` and `writer.sweep_code_structure` inside `ingest_roots`, so only one project+sweep generation executes at a time per process. Parse/map work stays outside the lock.

Add **`test_concurrent_ingest_is_serialized`** to prove a second ingest blocks until the first project+sweep completes and that the resulting graph retains a single `ingest_run_id`.

## Alternatives considered

- **Per-bank or per-root locks:** rejected for v1 — single configured coding bank and low concurrent reindex volume; global lock is simpler and matches service-scoped single-tenant v1.
- **Remove sweep from concurrent paths / queue jobs only:** rejected — HTTP reindex and scheduler both must remain callable; queue-only would still need the same serialization at the worker.
- **Optimistic concurrency with run ordering:** rejected — sweep Cypher is keyed on `ingest_run_id`, not run start time; ordering alone does not prevent A from sweeping B's rows.

## Consequences

**Easier:**

- Safe overlap between scheduled and manual reindex.
- Clear invariant for future ingest entry points: graph mutation phase is serialized.

**Harder:**

- Concurrent reindex requests block (latency under load, not correctness failure).

**Future chunks must know:**

- Extends ADR-134; does not replace it.
- **LUM-536** (deleted/renamed-file sweep) and **LUM-533** (ENTITY_TYPE_MAP sync) remain separate follow-ups.

## Revisit conditions

- Sustained need for parallel reindex (multi-bank or multi-tenant roots) → replace global lock with finer scope.
- Optional live FalkorDB HTTP concurrency integration test when KG CI slice is available.

## Linear linkage (Product OS)

- **LUM-301** — original ingest **Done** (ADR-134). Recommend **`/linear-update comment LUM-301`** linking ADR-141; no status reopen required unless Product OS splits hardening children.

## Testing retrospective

| Item | Detail |
| --- | --- |
| Tests added | `test_concurrent_ingest_is_serialized` |
| Commands | `lumogis-graph:test` image — `pytest tests/test_codegraph_ingest.py -v` |
| Results | 6 passed, 2 skipped (LUM-536 + FalkorDB integration) |
| Gaps | No route-level concurrent HTTP test; no live FalkorDB concurrency prove |
| Matrix | Same family as ADR-134 KG **3.2.16** — no new row required |

## Status history

- 2026-06-29: Finalised by /record-retro (retrospective); evidence merge `d85f7e428`, fix `f3dcc2832`.
