-- Migration 030: Async Ollama pull jobs (LUM-449)
-- Household-global job table for admin async pull + progress polling.

CREATE TABLE IF NOT EXISTS ollama_pull_jobs (
    job_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_name     TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
    progress_pct   SMALLINT,
    status_message TEXT,
    error_message  TEXT,
    qdrant_init_warning TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at     TIMESTAMPTZ,
    finished_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ollama_pull_jobs_running
    ON ollama_pull_jobs (status) WHERE status IN ('pending', 'running');
