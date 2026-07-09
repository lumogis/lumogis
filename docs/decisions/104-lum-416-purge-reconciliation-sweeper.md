# ADR-104: Partial-purge reconciliation sweeper (LUM-416)

**Status:** Finalised  
**Created:** 2026-06-18  
**Last updated:** 2026-06-18  
**Decided by:** as-shipped implementation (retrospective)  
**Finalised by:** /record-retro 2026-06-18 (Composer)  
**Plan:** none — shipped via Claude Code web batch  
**Exploration:** `.cursor/explorations/lum_416_purge_reconciliation_sweeper_retro.md`  
**Draft mirror:** `.cursor/adrs/lum-416-purge-reconciliation-sweeper.md`  
**Informed by:** ADR-074 (LUM-162 conversation purge), LUM-419

## Context

ADR-074 shipped bounded sync retry for conversation purge Qdrant/graph arms. LUM-416 is the background safety net for partial failures. Implementation also fixed `PurgeResult.partial` incorrectly reporting partial on clean session deletes (`qdrant_entities_deleted` default).

## Decision

1. **`PurgeResult(qdrant_entities_deleted=True)`** for session purge — restores correct `partial` semantics.
2. **Migration `034-purged-conversations-sweeper-columns.sql`** — extends `purged_conversations` with `qdrant_deleted`, `graph_deleted`, `errors`, `sweep_attempts`, `resolved_at`.
3. **`sweep_partial_purges()`** in `memory_purge.py` — retries failed arms; env-tuned via `LUMOGIS_PURGE_SWEEPER_*`.
4. **APScheduler job `purge_partial_sweep`** in `main.py` lifespan — `max_instances=1`, `coalesce=True`.

## Alternatives considered

- Re-run full `purge_session_memory` on sweep — rejected (coupling).
- `pg_cron` — rejected (extension dependency).

## Consequences

- Stranded conversation vector/graph artefacts eventually reconcile without operator intervention.
- Tombstones at max attempts require manual investigation (logged WARNING).

## Revisit conditions

- Partial rate >1% over 7 days — revisit sweep interval and alerting.
- Document purge sweeper if LUM-500 threshold breached (separate issue).

## Linear linkage (Product OS)

- **LUM-416** — Backlog in Linear at retro time; code on `integration/claude-2026-06-batch` @ `91d75ac76`. Close via `/linear-update apply-closure` after `dev` merge.

## Testing retrospective

- `orchestrator/tests/test_purge_sweeper.py` — **9 cases**; included in Phase 2 gate (**56 passed** combined backend suite).

## Status history

- 2026-06-18: Finalised by `/record-retro` (renumbered from mistaken `102-lum-416` on branch).
