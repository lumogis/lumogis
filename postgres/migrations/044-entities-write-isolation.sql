-- LUM-358: entity summary OCC + consolidation staging columns.
-- Additive idempotent migration; see .cursor/plans/LUM-358-household-concurrent-write-isolation.plan.md

BEGIN;

ALTER TABLE entities
    ADD COLUMN IF NOT EXISTS version BIGINT NOT NULL DEFAULT 1;

ALTER TABLE entities
    ADD COLUMN IF NOT EXISTS summary TEXT NOT NULL DEFAULT '';

ALTER TABLE entities
    ADD COLUMN IF NOT EXISTS staged_summary TEXT NULL;

COMMIT;
