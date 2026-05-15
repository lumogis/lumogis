-- Migration 022: durable action_proposals queue + atomic claim (LUM-123).
-- SPDX-License-Identifier: AGPL-3.0-only
--
-- Backs orchestrator/services/proposal_queue.py + actions/proposal_execute.py.
--
-- Status lifecycle (MVP): pending → approved → executing → done | dead,
-- with fail_execution mirroring user_batch_jobs backoff (back to approved until
-- max attempts). Stale executing/claimed rows are dead-lettered — never
-- re-queued to approved (see plan LUM-123).
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
