# ADR 040: Action proposals — atomic claim (mailbox pattern)

**Status:** Finalised
**Created:** 2026-05-14
**Last updated:** 2026-05-15
**Linear:** [LUM-123](https://linear.app/lumogis/issue/LUM-123/action-proposals-add-atomic-claim-to-prevent-double-execution-mailbox)
**Exploration:** `.cursor/explorations/LUM-123-action_proposals_atomic_claim.md`

## Context

Lumogis is moving to a household-scale Ask/Do model where one approved proposal can be visible to multiple clients. Without an atomic claim on `action_proposals`, the same approved row can execute twice — a data-loss class bug for connector writes. The solution must stay local-first on Postgres, match `batch_queue` patterns, and recover stale claims without re-opening successful side effects.

## Decision

Ship a new `action_proposals` table (migration **022**) with `user_id`, `claimed_at`, `claimed_by`, `run_after`, `attempt`, and a forward-compatible `status` CHECK (`pending` through `dead`). Implement **`services/proposal_queue`**: single-statement **`FOR UPDATE SKIP LOCKED`** dequeue (`claim_next`), conditional **`claim_by_id`**, `mark_done`, `fail_execution` (mirror **`batch_queue`** backoff / dead threshold), and **`reset_stuck_claims`** that moves stale **`executing`** / **`claimed`** rows to **`dead`** with error **`stale_executing_claim`** — **never** back to **`approved`**. Wire **`actions/proposal_execute.claim_and_execute_proposal`**, **`POST /api/v1/approvals/proposals/{proposal_id}/execute`** (rate-limited, `as_user` via admin resolution), and an APScheduler stuck sweeper controlled by env vars. Lost races emit **`audit.proposal.lost_claim`** on **`lumogis.audit`** (structlog **only**). **`action_proposals`** is listed in **`_OMITTED_USER_TABLES`** for per-user export (operational queue, like **`user_batch_jobs`**).

## Alternatives considered

See exploration doc — advisory locks only, SERIALIZABLE, Redis, external queue libraries, and in-process locks were rejected for this scope.

## Consequences

- **LUM-100** must align lifecycle/DTO and ingestion with this schema; extensions require new migrations.
- **LUM-147** / **LUM-191** must use **`claim_next`** / **`claim_by_id`** — not raw **`execute`** on proposal payloads.
- Operators tune **`ACTION_PROPOSALS_CLAIM_STUCK_AFTER_SECONDS`** (default **300**) vs long-running connectors; per-action TTL remains a **LUM-100** follow-up.

## Status history

- 2026-05-14: Draft created via `/explore` + `/create-plan` / `/review-plan` (revised structlog-only lost-claim; stale **`executing` → `dead`**).
- 2026-05-15: **Finalised** by `/verify-plan --headless` — implementation confirmed; canonical copy here.
