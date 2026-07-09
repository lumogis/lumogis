# ADR-096: Hub library index cold-start resync when defer flag set

**Status:** Finalised
**Created:** 2026-06-11
**Last updated:** 2026-06-11
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-06-11 (Composer)
**Plan:** none — shipped before formal plan / verify cycle for this chunk
**Exploration:** `.cursor/explorations/hub-library-index-cold-start-resync_retro.md`
**Draft mirror:** `.cursor/adrs/hub-library-index-cold-start-resync.md`

## Context

Bundled Lumogis Hub sets **`LUMOGIS_DEFER_LIBRARY_INDEX`** so the first bulk library scan runs only after the setup wizard calls **`POST /bundled/start-library-index`** (see wizard step 6, commit `6f43358e6`). That correctly blocks indexing during first-run onboarding.

After onboarding, operators could add files to configured ingest folders while Hub was **fully quit**. On the next cold start, Core still saw the defer flag and **skipped** any bulk resync. Event-only ingest watchers only observe live filesystem events, so those files were never indexed and memory search stayed stale until a manual re-index.

The fix shipped on **`dev`** via Cursor branch `critical-bug-investigation-fa4b` (`0d668084f`, merge `f46013440`) without a prior plan/verify loop.

## Decision

When **`LUMOGIS_DEFER_LIBRARY_INDEX`** is active:

1. **First run (empty index):** behaviour unchanged — log deferral; wizard triggers **`/bundled/start-library-index`**.
2. **Cold start with prior index:** if the embedder is ready and **`prior_library_index_exists()`** (`file_index` row count > 0 for ingest owner), call **`enqueue_initial_ingest_scan()`** at lifespan startup.
3. **Embedding readiness retry:** same resync branch when defer is set and prior index exists (covers embedder warming after startup).

**`prior_library_index_exists()`** lives in **`orchestrator/services/index_bootstrap.py`** and reuses **`_file_index_count()`**.

Non-defer deployments (default Docker Compose) are unaffected.

### As-implemented surface

| Area | Detail |
| --- | --- |
| Env | `LUMOGIS_DEFER_LIBRARY_INDEX` ∈ `1` / `true` / `yes` (case-insensitive) |
| Helper | `prior_library_index_exists()` → `bool` |
| Startup | `orchestrator/main.py` lifespan — defer branch + embedder-ready gate |
| Retry job | `embedding_readiness_retry` — defer + prior index branch |
| Scan entry | existing `enqueue_initial_ingest_scan()` (idempotent) |

## Alternatives considered

- **Clear defer flag after wizard** — rejected: Hub supervisor/env would need another persistence channel; `file_index` presence is sufficient signal.
- **Always scan on defer restart** — rejected: would re-run heavy bulk scan on every restart even mid-wizard recovery edge cases.
- **Hub-only sidecar trigger** — rejected: resync belongs in Core ingest bootstrap; same code path as Docker once defer is used elsewhere.

## Consequences

- Search index catches up after full-quit file drops without operator manual re-index.
- Wizard first-run semantics preserved when `file_index` is empty.
- Core orchestrator change ships in public AGPL tree (env-gated; primary consumer is bundled Hub today).

## Revisit conditions

- `file_index` ownership / multi-user ingest model changes — revisit count scope in `prior_library_index_exists()`.
- Hub wizard reset flows that truncate index without re-onboarding.
- Move to 100% event-driven ingest with guaranteed backfill on startup — may obsolete folder scan resync.

## Linear linkage (Product OS)

- **Issue:** [LUM-477](https://linear.app/lumogis/issue/LUM-477/hub-library-index-cold-start-resync-when-defer-flag-set) (Done — backfilled 2026-06-11)
- **Parent:** [LUM-396](https://linear.app/lumogis/issue/LUM-396/bundled-track-sidecar-process-manager-core-qdrant-ollama-background)
- **P2 child:** [LUM-478](https://linear.app/lumogis/issue/LUM-478/p2-integration-test-for-hub-defer-cold-start-library-resync) — integration/lifespan test gap

## Testing retrospective

- **Unit tests added:** `test_prior_library_index_exists_false_without_rows`, `test_prior_library_index_exists_true_when_rows_present` in `orchestrator/tests/test_index_bootstrap.py`.
- **Run:** `pytest orchestrator/tests/test_index_bootstrap.py` — **6 passed** post-merge.
- **Gap closed (LUM-478):** five lifespan integration tests added in the same file:
  - `test_lifespan_defer_resync_enqueues_when_prior_index_exists` — happy path: defer + resync flags set, embedder ready, prior index present → `enqueue_initial_ingest_scan` called once. Patches `_file_index_count` (not `prior_library_index_exists`) so the real helper runs.
  - `test_lifespan_defer_skips_resync_when_no_prior_index` — first-run wizard path: same flags, empty `file_index` → no enqueue.
  - `test_lifespan_defer_skips_resync_when_embedder_not_ready` — embedder cold: prior index present but embedder reports not ready → no enqueue (retry job handles it).
  - `test_lifespan_defer_skips_resync_when_resync_flag_absent` — explicit opt-in gate: prior index + ready embedder but `LUMOGIS_LIBRARY_RESYNC_ON_START` unset → no enqueue.
  - `test_embedding_readiness_retry_enqueues_resync_when_prior_index` — ADR-096 branch #3: lifespan starts with cold embedder, retry closure is captured via `scheduler.add_job` spy and invoked directly; verifies `enqueue_initial_ingest_scan` called once when embedder warms.
- **Prove:** `pytest orchestrator/tests/test_index_bootstrap.py` — **12 passed** (LUM-478).

## Status history

- 2026-06-11: Finalised by `/record-retro` (retrospective).
- 2026-06-11: Linear backfill — **LUM-477** Done; **LUM-478** P2 child filed.
