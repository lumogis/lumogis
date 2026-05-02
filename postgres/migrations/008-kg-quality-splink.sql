-- Migration 008: KG Quality Pipeline Pass 4b — Splink probabilistic deduplication
-- deduplication_runs: lifecycle record for each automated dedup job execution
-- dedup_candidates: scored candidate pairs produced by Splink, routed to review_queue

CREATE TABLE IF NOT EXISTS deduplication_runs (
    run_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             TEXT NOT NULL DEFAULT 'default',
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMPTZ,
    candidate_count     INT,
    auto_merged         INT,
    queued_for_review   INT,
    known_distinct      INT,
    error_message       TEXT
);

CREATE TABLE IF NOT EXISTS dedup_candidates (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              UUID NOT NULL REFERENCES deduplication_runs(run_id) ON DELETE CASCADE,
    user_id             TEXT NOT NULL DEFAULT 'default',
    entity_id_a         UUID NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    entity_id_b         UUID NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    match_probability   DOUBLE PRECISION NOT NULL,
    features            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (entity_id_a < entity_id_b)
);

CREATE INDEX IF NOT EXISTS idx_dedup_candidates_run
    ON dedup_candidates (run_id, match_probability DESC);

CREATE INDEX IF NOT EXISTS idx_dedup_candidates_user
    ON dedup_candidates (user_id, match_probability DESC)
    WHERE match_probability >= 0.5;
