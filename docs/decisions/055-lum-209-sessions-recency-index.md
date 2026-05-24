# ADR-055: LUM-209 — `sessions` recency composite index (column/trigger already shipped)

**Status:** Accepted
**Created:** 2026-05-21
**Last updated:** 2026-05-21
**Decided by:** `/explore --headless` LUM-209 (draft); **`/verify-plan --headless`** finalisation
**Linear:** [LUM-209](https://linear.app/lumogis/issue/LUM-209/sessions-table-missing-updated-at-add-column-trigger-alembic-migration)

## Context

LUM-209 originally asked for `sessions.updated_at`, a BEFORE UPDATE trigger, an Alembic migration, backfill, and a recency index. Repo evidence shows **`postgres/migrations/003-sessions-notes-audio-graph-tracking.sql`** already created **`updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`**, the shared **`update_updated_at_column()`** function, and the **`set_updated_at`** trigger on **`sessions`**. Lumogis applies ordered SQL under **`postgres/migrations/`** via **`orchestrator/db_migrations.py`** and a **`schema_migrations`** ledger — there is **no** Alembic migration path in the product tree.

The remaining gap was query performance: **`orchestrator/services/memory.py::recent_sessions`** filters with **`visibility.visible_filter`** (default union of personal rows for the caller’s **`user_id`** plus household **`shared`** / **`system`** scopes) and **`ORDER BY updated_at DESC`**. Without a matching index, Postgres tends toward **`Seq Scan` + `Sort`** as **`sessions`** grows.

## Decision

1. Ship **`postgres/migrations/024-sessions-user-updated-at-index.sql`** with **`CREATE INDEX IF NOT EXISTS idx_sessions_user_updated_at ON sessions (user_id, updated_at DESC);`** (idempotent; one migration file per runner transaction).

2. Treat LUM-209’s column/trigger/backfill/migration-file expectations as **already satisfied by 003**. Close the Linear bug with a **`/linear-update`** comment that corrects the “Alembic” wording — canonical migrations are SQL files + **`db_migrations.py`**, not Alembic.

3. **Index shape:** **`(user_id, updated_at DESC)`** optimises the high-cardinality **`scope='personal' AND user_id = %s`** arm of the default visibility union. Narrow **`scope_filter='shared'`** / **`'system'`** reads omit **`user_id`** equality and may not use this btree alone; that is acceptable at current household scale (see plan exploration).

## Alternatives Considered

- **Single-column `(updated_at DESC)` index** — poor fit for **`user_id`‑first** selective reads; rejected.
- **`last_modified_at` column** — redundant with **`updated_at`**; rejected.
- **Introduce Alembic** — architectural fork; out of scope for LUM-209; rejected.
- **`CREATE INDEX CONCURRENTLY`** — incompatible with the current per-file transactional apply in **`db_migrations.py`**; defer unless migration-time locks become an operational issue (**track under batch/ops work such as LUM-25**).

## Consequences

**Positive:** `recent_sessions` / MCP **`memory.get_recent`** and future LUM-116 / LUM-201 style recency consumers get an index-backed path for the personal arm; **`schema_migrations`** records **`024-sessions-user-updated-at-index.sql`** once.

**Trade-offs:** Boot-time index build holds a lock for the duration of that migration transaction (acceptable at v0.1 row counts). Shared/system-only recency slices rely on planner behaviour without a dedicated partial index in this chunk.

## Revisit conditions

- Large **`sessions`** tables where index creation exceeds acceptable migration windows → revisit **`CONCURRENTLY`** plus runner transaction semantics.
- Workloads that scan **`sessions.updated_at`** without **`user_id`** predicates (e.g. global incremental sync) → consider an additional **`(updated_at DESC)`** index; track with LUM-116 / LUM-201 when those designs firm up.
- Any future Alembic adoption → separate ADR; this file remains the record for the **024** btree shipped under the SQL-file runner.

## Status history

- 2026-05-21: Draft created from exploration **`.cursor/explorations/LUM-209-sessions-updated-at-index.md`**.
- 2026-05-21: Finalised by **`/verify-plan --headless`** — migration **024** applied in compose boot; **`schema_migrations`** + **`pg_indexes`** verified; **`make compose-test`** + **`make compose-test-stack-control`** green.
