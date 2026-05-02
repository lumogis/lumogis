-- Migration 017: per-user durable batch job ledger.
-- SPDX-License-Identifier: AGPL-3.0-only
--
-- Closes audit B7. Backs orchestrator/services/batch_queue.py.
-- See ADR `per_user_batch_jobs.md` and plan
-- `.cursor/plans/per_user_batch_jobs.plan.md`.
--
-- Ownership: every row is owned by exactly one user_id. AUTH_ENABLED=false
-- single-operator installs use the same 'default' sentinel used by other
-- per-user tables until db_default_user_remap.py rewrites it on first
-- AUTH_ENABLED=true boot.
--
-- Status lifecycle: pending → running → done | failed-and-retried
-- (back to pending) → dead. `running` rows whose started_at is older
-- than BATCH_QUEUE_STUCK_AFTER_SECONDS are reset to pending by the
-- sweeper job (orchestrator/main.py).
--
-- Idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS user_batch_jobs (
    id            BIGSERIAL PRIMARY KEY,
    user_id       TEXT        NOT NULL DEFAULT 'default',
    kind          TEXT        NOT NULL,
    payload       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    status        TEXT        NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','running','done','dead')),
    attempt       INTEGER     NOT NULL DEFAULT 0,
    run_after     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    enqueued_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at    TIMESTAMPTZ NULL,
    finished_at   TIMESTAMPTZ NULL,
    error         TEXT        NULL,
    worker_id     TEXT        NULL
);

CREATE INDEX IF NOT EXISTS user_batch_jobs_pending_claim_idx
    ON user_batch_jobs (run_after, id)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS user_batch_jobs_running_per_user_idx
    ON user_batch_jobs (user_id)
    WHERE status = 'running';

CREATE INDEX IF NOT EXISTS user_batch_jobs_running_started_idx
    ON user_batch_jobs (started_at)
    WHERE status = 'running';

CREATE INDEX IF NOT EXISTS user_batch_jobs_user_status_idx
    ON user_batch_jobs (user_id, status);

COMMIT;
