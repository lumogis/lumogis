# ADR: Per-user batch jobs and durable background work

**Status:** Finalised  
**Created:** 2026-04-20  
**Last updated:** 2026-04-22  
**Decided by:** /explore (Composer) — v2 refresh pass; /create-plan (Composer) — revisit triggers; /verify-plan — implementation confirmed

## Context

Lumogis is moving to explicit per-user isolation (`user_id` threading, family-LAN multi-user). Background and batch work must remain attributable, fairly scheduled, and durable across process restarts. The orchestrator already uses APScheduler and FastAPI `BackgroundTasks`, but these mechanisms do not provide a first-class per-user job ledger or fix the current routine scheduling gap where job ids omit `user_id`. Audit **B7** (cross-linked from `docs/decisions/022-ntfy-runtime-per-user-shipped.md`) tracks this gap. The option space spans custom Postgres queues, Postgres-native libraries (Procrastinate, Chancy, Oban-py), Redis-broker systems (arq, Dramatiq), and full-broker frameworks (Celery).

**v2 refresh (2026-04-22)** — Migration **016** lifted `routine_do_tracking` to per-`user_id`, which makes the still-broken cron callback in `orchestrator/services/routines.py` (`id=f"routine_{spec.name}"`, `_job_callback(name)` → `run_routine(name)` without the required `user_id` kwarg) actively misleading: storage assumes per-user, scheduling does not. Refreshed evidence on libraries (Procrastinate **3.7.3**, released 2026-03-28; Chancy **v0.24.3**, released 2025-07-21) plus the Hatchet-documented per-tenant `FOR UPDATE SKIP LOCKED` pattern (with the explicit warning that `PARTITION BY` window functions are incompatible with `FOR UPDATE`) tightened the option ratings without changing the direction. See *(maintainer-local only; not part of the tracked repository)*.

## Decision

**Adopt a Postgres-backed per-user batch job table (`user_batch_jobs`) processed by a small in-repo worker loop driven by APScheduler (or equivalent periodic tick), without adding a new Docker service or mandatory Redis broker for the default install.** Use `SELECT … FOR UPDATE SKIP LOCKED` for claim, with naive FIFO + per-user `max_concurrent` ceiling in v1 and the option to escalate to the Hatchet round-robin/pointer pattern when starvation is observed. Concurrently, **repair routine scheduling in the same chunk** so APScheduler job identifiers and callbacks are scoped by `user_id` (`id=f"routine_{spec.user_id}_{spec.name}"`, `_job_callback(name, user_id)` → `run_routine(name, user_id=user_id)`) and the existing `tests/test_phase3_user_id_contracts.py::test_run_routine_requires_user_id_kwarg` contract is honoured by the cron path. Defer adoption of third-party queue frameworks until operational complexity justifies them.

## Alternatives Considered

- **Procrastinate 3.7.3 (MIT, 2026-03-28)** — actively maintained Postgres-backed task queue with FastAPI `lifespan` integration documented; uses `LISTEN/NOTIFY` + `SELECT FOR UPDATE`. Manages its own tables outside our migration numbering, **no built-in per-user fairness primitive** (queue-per-user is the workable layer-on), and the upstream README is currently flagging a maintainer search. Defer until our custom-queue maintenance cost outgrows comfort. See *(maintainer-local only; not part of the tracked repository)*.
- **Chancy v0.24.3 (2025-07-21)** — async-first, built-in dashboard, runtime queue management; **no built-in fairness primitive**, smaller community, cadence has slowed since mid-2025. Strongest case is the dashboard once we have a queue-UX requirement (we do not in v1).
- **Oban-py** — Postgres-native, richer fairness primitives than Procrastinate; revisit if we commit to Python 3.12+ only.
- **Huey (SQLite/Redis)** — second persistence store or broker dependency; not the default path when Postgres is already authoritative.
- **arq, Celery, Dramatiq** — broker-centric or maintenance-mode concerns for the default local-first persona; would add Redis to the default install.

## Consequences

**Easier:** Per-user work becomes queryable and auditable in SQL; backups include pending batches; single-stack operators need only Postgres; no new container types for the happy path; the routine-id fix lands inside the same chunk that introduces the queue, so the Phase 3 cron contract is no longer "approved-but-incorrect".

**Harder:** The project owns retry semantics, poison-pill handling, fairness policies, and observability surface area until/unless a library is adopted later. Operators looking for a built-in dashboard get logs only in v1.

**Future chunks must know:** Any new multi-step background pipeline should enqueue through this mechanism (or an adopted library) rather than spawning unbounded threads or scheduling unscoped APScheduler jobs; the five current `BackgroundTasks` callsites (3× in `orchestrator/routes/data.py`, 2× in `orchestrator/routes/admin.py`) are first-class migration candidates and should be moved as part of v1 or as named follow-up chunks. Graph-service-owned jobs (`services/lumogis-graph`) remain a separate integration boundary until explicitly unified by a future ADR.

## Revisit conditions

- If **multiple orchestrator replicas** become supported without sticky sessions, revisit distributed locking / shared queue notification (Postgres `LISTEN/NOTIFY` over our table, or migrate to Procrastinate/Chancy).
- If **per-user routine count** or **job volume** exceeds comfortable APScheduler tick frequency (e.g. thousands of users × sub-minute crons), move scheduling state into Postgres-backed triggers or a dedicated library.
- If the team **commits to Python 3.12+ only** and wants less custom SQL, re-evaluate **Oban-py** or **Procrastinate** as a drop-in replacement for the custom table.
- If a **first-party queue dashboard** becomes a v-next product requirement, re-evaluate **Chancy** specifically.
- If we **observe per-user starvation** under naive FIFO, switch the worker claim path to the Hatchet round-robin/pointer pattern (per-tenant `WHERE user_id=… LIMIT n FOR UPDATE SKIP LOCKED`) — the table shape is forward-compatible with this change.
- If an **operator needs queue triage** ("why is mum's ingest stalled?"), ship the deferred admin projection (`GET /api/v1/admin/batch-jobs?user_id=&kind=&status=`). Logs-only is the v1 norm; this is the trigger to add a structured surface.
- If **dead rows become a recurring operational problem**, revisit emitting `__batch_job__.dead` audit events (`actions/audit.py::write_audit`). v1 keeps the `user_batch_jobs` table itself as the audit/history record (no queue-lifecycle audit rows); per-kind retry policies and event emission move from "table-only" to "table + audit" together.
- If a **household asks for separate worker scaling** (or the orchestrator GIL becomes a contention source for queue throughput), ship the deferred sidecar entrypoint (`python -m batch_queue.worker`). The substrate exposes `_run_one_tick(worker_id)` already; the sidecar is ~10 LOC of loop, no `services/batch_queue.py` change required.

## Status history

- 2026-04-20: Draft created by /explore (Composer) — Option 1 (custom Postgres `user_batch_jobs` + APScheduler sweeper) selected; routine-id fix bundled.
- 2026-04-22: Draft revised by /explore v2 (Composer) — context refreshed against migration 016 + 2026 library releases (Procrastinate 3.7.3, Chancy v0.24.3) + Hatchet fairness pattern; revisit conditions expanded; alternatives re-rated; direction unchanged. See *(maintainer-local only; not part of the tracked repository)*.
- 2026-04-22: Revisit triggers extended by /create-plan (Composer) — three explicit follow-up triggers added (operator-triage projection, dead-row audit emission, sidecar worker scaling) so the v1 logs-only / table-only / in-process-only choices have named exits. Plan written to *(maintainer-local only; not part of the tracked repository)*.
- 2026-04-22: Finalised by /verify-plan — implementation confirmed against the decision above; draft mirror updated at *(maintainer-local only; not part of the tracked repository)*.
