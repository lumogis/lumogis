# ADR 040: Action proposals — atomic claim (mailbox pattern)

**Status:** Finalised
**Created:** 2026-05-14
**Last updated:** 2026-08-03 (amended by LUM-762 — see Consequences and Status history)
**Linear:** [LUM-123](https://linear.app/lumogis/issue/LUM-123/action-proposals-add-atomic-claim-to-prevent-double-execution-mailbox)
**Exploration:** `.cursor/explorations/LUM-123-action_proposals_atomic_claim.md`

## Context

Lumogis is moving to a household-scale Ask/Do model where one approved proposal can be visible to multiple clients. Without an atomic claim on `action_proposals`, the same approved row can execute twice — a data-loss class bug for connector writes. The solution must stay local-first on Postgres, match `batch_queue` patterns, and recover stale claims without re-opening successful side effects.

## Decision

Ship a new `action_proposals` table (migration **022**) with `user_id`, `claimed_at`, `claimed_by`, `run_after`, `attempt`, and a forward-compatible `status` CHECK (`pending` through `dead`). Implement **`services/proposal_queue`**: single-statement **`FOR UPDATE SKIP LOCKED`** dequeue (`claim_next`), conditional **`claim_by_id`**, `mark_done`, `fail_execution` (mirror **`batch_queue`** backoff / dead threshold), and **`reset_stuck_claims`** that moves stale **`executing`** / **`claimed`** rows to **`dead`** with error **`stale_executing_claim`** — **never** back to **`approved`**. Wire **`actions/proposal_execute.claim_and_execute_proposal`**, **`POST /api/v1/approvals/proposals/{proposal_id}/execute`** (rate-limited, `as_user` via admin resolution), and an APScheduler stuck sweeper controlled by env vars. Lost races emit **`audit.proposal.lost_claim`** on **`lumogis.audit`** (structlog **only**). **`action_proposals`** is listed in **`_OMITTED_USER_TABLES`** for per-user export (operational queue, like **`user_batch_jobs`**).

## Alternatives considered

See exploration doc — advisory locks only, SERIALIZABLE, Redis, external queue libraries, and in-process locks were rejected for this scope.

## Consequences

> **⚠️ Superseded in part by LUM-762 (2026-08-03).** The three bullets below were written on the assumption that a background drain would follow. It never did — `claim_next` had zero production callers for three releases — and LUM-762 decided it never will. **`claim_next` and the `fail_execution` retry ladder are deleted.** Read the bullets as historical; the amendments beneath each are current.

- **LUM-100** must align lifecycle/DTO and ingestion with this schema; extensions require new migrations.
  - **Amended:** the lifecycle it aligns to is now **one-way** — `pending → approved → executing → done | dead`, no `executing → approved` edge. `approved` is **momentary**, not a durable queue state.
- ~~**LUM-147** / **LUM-191** must use **`claim_next`** / **`claim_by_id`** — not raw **`execute`** on proposal payloads.~~
  - **Amended:** `claim_next` no longer exists. **LUM-147** owns `run_after` for its `NOT_NOW` deferral (not as a retry clock), and its archive job need only consider `pending` — an `approved` row can no longer accumulate. **LUM-191** (routine auto-execution) should follow `services/routines.py`'s **direct-execute** path, which already runs actions in the background without this table; a queue would add nothing and would reintroduce duplicate-execution exposure for LOW-risk unattended actions.
- Operators tune **`ACTION_PROPOSALS_CLAIM_STUCK_AFTER_SECONDS`** (default **300**) vs long-running connectors; per-action TTL remains a **LUM-100** follow-up.
  - **Amended:** **`ACTION_PROPOSALS_MAX_ATTEMPTS`** is removed. **`ACTION_PROPOSALS_QUEUE_ENABLED`** is renamed **`ACTION_PROPOSALS_SWEEPER_ENABLED`** (legacy honoured one release) because it gated a sweeper and never gated a queue.

## Status history

- 2026-05-14: Draft created via `/explore` + `/create-plan` / `/review-plan` (revised structlog-only lost-claim; stale **`executing` → `dead`**).
- 2026-05-15: **Finalised** by `/verify-plan --headless` — implementation confirmed; canonical copy here.
- 2026-08-03: **Amended by LUM-762** — the drain this ADR anticipated was never built and will not be. `claim_next`, `_CLAIM_NEXT_SQL` and `fail_execution` are deleted; failure is terminal on the first attempt; `dead_letter` is the sole disposal. The **atomic claim survives unchanged** and remains this ADR's live contribution — it is reached on every execution and exists for the household condition this ADR named: one approved proposal, several clients, either can press Execute. See `docs/decisions/201-lum-762-action-proposals-claim-not-queue.md`.
