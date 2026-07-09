-- Migration 035: ingest job progress columns on user_batch_jobs (LUM-511)
-- SPDX-License-Identifier: AGPL-3.0-only

BEGIN;

ALTER TABLE user_batch_jobs
  ADD COLUMN IF NOT EXISTS progress_stage TEXT NULL,
  ADD COLUMN IF NOT EXISTS progress_pct SMALLINT NULL
    CHECK (progress_pct IS NULL OR (progress_pct >= 0 AND progress_pct <= 100)),
  ADD COLUMN IF NOT EXISTS progress_message TEXT NULL;

CREATE INDEX IF NOT EXISTS user_batch_jobs_batch_id_idx
  ON user_batch_jobs (user_id, ((payload->>'batch_id')))
  WHERE payload ? 'batch_id';

COMMIT;
