-- Migration 022: durable action_proposals table + atomic claim (LUM-123).
-- NOTE: this is a CLAIM, not a queue — nothing drains it (LUM-762). See below.
-- SPDX-License-Identifier: AGPL-3.0-only
--
-- Backs orchestrator/services/proposal_queue.py + actions/proposal_execute.py.
--
-- Status lifecycle: pending → approved → executing → done | dead. ONE-WAY —
-- there is deliberately no executing → approved edge (LUM-762). Failure is
-- terminal on the first attempt; dead_letter is the sole disposal. Stale
-- executing/claimed rows are dead-lettered by the sweeper, never re-queued.
--
-- This header previously described fail_execution mirroring user_batch_jobs
-- backoff (back to approved until max attempts). That mechanism never ran —
-- nothing drained the queue — and it was removed in LUM-762 rather than wired,
-- because a redrive of an unkeyed third-party SMTP submission is a duplicate
-- email, not a retry. See docs/decisions/040-action-proposals-atomic-claim.md.
--
-- `attempt` and `run_after` are both RETAINED, with different owners:
--   * `run_after` is no longer written here at all — it belongs to LUM-147's
--     NOT_NOW deferral. `_CLAIM_BY_ID_SQL` filters on it so it can never again
--     be advisory.
--   * `attempt` is written ONCE, by `dead_letter` (0 = never attempted,
--     1 = attempted and failed). Note the stale-claim sweeper does NOT bump it,
--     so a swept row reads 0 despite having been attempted — read `error`, not
--     the counter, to tell those apart.
--
-- Manual downgrade: DROP TABLE action_proposals; (operator-only, no automated
-- rollback in-repo.)
--
-- Idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS action_proposals (
    id              BIGSERIAL PRIMARY KEY,
    user_id         TEXT        NOT NULL DEFAULT 'default',
    action_name     TEXT        NOT NULL,
    payload         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    status          TEXT        NOT NULL DEFAULT 'pending'
                    CHECK (status IN (
                      'pending','approved','claimed','executing',
                      'done','rejected','archived','dead'
                    )),
    attempt         INTEGER     NOT NULL DEFAULT 0,
    run_after       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    claimed_at      TIMESTAMPTZ NULL,
    claimed_by      TEXT        NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    executed_at     TIMESTAMPTZ NULL,
    finished_at     TIMESTAMPTZ NULL,
    error           TEXT        NULL
);

CREATE INDEX IF NOT EXISTS action_proposals_approved_claim_id_idx
    ON action_proposals (id)
    WHERE status = 'approved' AND claimed_at IS NULL;

CREATE INDEX IF NOT EXISTS action_proposals_active_claim_ts_idx
    ON action_proposals (claimed_at)
    WHERE status IN ('claimed','executing');

COMMIT;
