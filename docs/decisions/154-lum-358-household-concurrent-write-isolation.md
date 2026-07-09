# ADR-154: Household concurrent write isolation for the knowledge graph (LUM-358)

**Status:** Finalised

**Created:** 2026-07-05

**Last updated:** 2026-07-05

**Decided by:** `/explore --headless LUM-358`; implemented per `.cursor/plans/LUM-358-household-concurrent-write-isolation.plan.md`

**Finalised by:** /verify-plan --headless 2026-07-05 (Composer)

**Plan:** `.cursor/plans/LUM-358-household-concurrent-write-isolation.plan.md`

**Exploration:** `.cursor/explorations/LUM-358-household-concurrent-write-isolation.md`

**Draft mirror:** `.cursor/adrs/LUM-358-household-concurrent-write-isolation.md`

**Linear:** [LUM-358](https://linear.app/lumogis/issue/LUM-358/explore-household-concurrent-write-isolation-consolidation-lock-scope)

## Context

The household knowledge graph is gaining three concurrent write paths that today share no isolation contract: a background **sleep-time consolidation agent** rewriting entity summaries (LUM-109/106), **LLM write-back** tools mutating entities during live sessions (LUM-108), and **multiple household members** writing shared-scope facts (LUM-334, shipped). The dangerous case is a long read-modify-write: consolidation reads an entity summary, spends seconds-to-minutes in local LLM inference, then writes back — silently clobbering any user edit that landed in between (`risk:data-loss`). Constraints: single Core, local-first, no new Docker service, prefer Postgres/in-process coordination, and fit the existing five-concept model. FalkorDB already serialises writes per graph and graph projection is idempotent/replayable, so the race is not in the store — it is the **app-level cross-store (Postgres + FalkorDB) read-modify-write of the entity summary**.

## Decision

Adopt **optimistic concurrency control (OCC) as the entity-write contract**, coordinated by a **per-`(scope-owner, entity_type)` Postgres advisory-lock singleton** for consolidation runs (cross-process only; in-process coalescing via APScheduler like `digest.py`), with **staged-write promotion**:

- Consolidation acquires a `pg_try_advisory_lock` on a **dedicated checkout connection** (not the process-wide singleton `_conn`) keyed on salt `8421358` + `(scope_owner, entity_type)` (mirroring `orchestrator/signals/digest.py` cross-process semantics, ADR 022); it holds **no** DB lock across LLM inference.
- Every entity-summary write commits through `services.entity_write_guard` — `UPDATE entities SET summary=…, version=version+1 WHERE entity_id=$id AND version=$read_version`; absent `RETURNING` means a concurrent write intervened → **compensate (re-read + merge or defer), never blind-retry**. Consolidation writes land in **`staged_summary`** (dedicated column — not `is_staged` overload) and are promoted atomically under the version guard.
- Multi-user writes use a **field-tier policy** (`services.entity_conflict_policy`): low-risk structured metadata → newest-wins/set-union; freeform summary → OCC version guard; genuinely divergent **shared-scope** entities reuse LUM-514's represent-both / review-queue vocabulary at synthesis time (distinct storage layer, not conflated). **`system`-scope summaries are read-only** for household members in the foundation contract (admin/system-writer path deferred).
- Lease/heartbeat row crash-recovery (exploration Option 3 prose) **deferred** to a follow-up Linear issue; v1 uses non-blocking try + APScheduler coalesce.

Schema: migration **044** adds `entities.version`, `entities.summary`, `entities.staged_summary`. Library modules ship without new HTTP routes; LUM-108/109/106 consumers wire in later chunks behind existing `LUMOGIS_FF_*` flags (default off).

## Alternatives Considered

- **Pessimistic global consolidation lock** — freezes household writes for minutes and leaks session locks across inference; rejected.
- **Epoch-based isolation (`consolidation_epoch`, Option C)** — correct but over-engineered for low household contention; useful core reduces to the OCC version column.
- **Lease table / Redis TTL leases** — solves lock lifecycle but not the read-modify-write race; adds machinery without solving RMW alone.
- **Serializable txn / CRDTs / version-vectors / FalkorDB-level locking** — ruled out (cannot span LLM inference; no replication topology; FalkorDB guards intra-store only).

## Consequences

**Easier:** live sessions stay responsive (no lock across inference); different entity types consolidate in parallel; LUM-109/108/106/354/356 build against one contract; reuses shipped primitives (advisory lock pattern, `staged_summary` column, monotonic version columns, LUM-514 vocabulary).

**Harder / foreclosed:** Postgres becomes the write-coordination **authority** — LUM-359's sidecar cannot own the lock. OCC version semantics become mandatory for `entity_update()` (LUM-108) and validation hooks (LUM-354). Every entity field must declare a conflict **tier**. FalkorDB summary projection **must** follow OCC-promoted Postgres rows — never parallel cross-store RMW.

**Future chunks must know:** read version before slow work; acquire consolidation lock only for fast claim/stage; release before LLM; commit through `entity_write_guard`; do not wrap guard calls in `PostgresStore.transaction()` on the shared `_conn`.

## Crash recovery (v1)

Consolidation runs use **non-blocking** `pg_try_advisory_lock` on a **dedicated checkout connection** (not the process-wide singleton `_conn`). In-process duplicate runs coalesce via APScheduler `max_instances=1` (same pattern as `signals.digest`).

When a worker process exits, Postgres releases session advisory locks held on that backend automatically — no orphan lock survives process death. The remaining edge case is a **live but broken singleton session** on the digest path: `PostgresStore._ensure_conn()` reconnect drops any advisory lock held on `_conn` (proved in `orchestrator/tests/integration/test_postgres_store_advisory_reconnect.py`). At household scale digest coalesce via APScheduler is sufficient; consolidation avoids the singleton path entirely.

An optional **lease/heartbeat row** (exploration Option 3 prose) remains a future hardening path when LUM-109 ships multi-worker consolidation or PgBouncer transaction-pooling — not required for the foundation contract.

## Revisit conditions

- **PgBouncer transaction-mode** in front of Core → switch to transaction-scoped `pg_try_advisory_xact_lock` around only the fast promote step, or a lease-row model.
- **LUM-109 multi-worker consolidation** without APScheduler coalesce → revisit lease/heartbeat row for bounded crash recovery.
- Entity-write **conflict rate exceeds ~20%** → revisit pessimistic per-entity locking or event-sourced summaries.
- **Multi-Core / replicated** topology → revisit CRDT/version-vector convergence.
- Full **edit history / attribution** needed → revisit event-sourced revision log instead of single-row OCC.

## Status history

- 2026-07-05: Draft created by /explore --headless LUM-358
- 2026-07-05: Revised during /review-plan --arbitrate R1 — dedicated checkout connection; `staged_summary` column; system-scope read-only; lease-row deferred
- 2026-07-05: Finalised by /verify-plan --headless — implementation confirmed (migration 044, OCC guard, consolidation lock, conflict policy, PoC unit tests)
