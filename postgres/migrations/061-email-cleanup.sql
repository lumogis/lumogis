-- LUM-646 (Inbox cleanup, chunked): on-demand date-range cleanup passes + a separate
-- "empty the spam folder to Trash" pass. Everything is user-scoped and reversible via
-- the LUM-645 email_organize_log ledger. All new state is additive; existing installs
-- get empty tables and one nullable ledger column, so behaviour is unchanged until a
-- user starts a cleanup job.
--
-- Two job kinds (email_cleanup_job.kind):
--   'window' -- process a custom [window_start, window_end] date range: backfill+classify
--               into signals, auto-label (label-visible providers), then execute
--               user-approved move-to-Lumogis-folder actions. Never trashes INBOX mail.
--   'spam'   -- empty the provider's \Junk special-use folder to \Trash (the only
--               destructive path; provider-confirmed spam; reversible).
--
-- Reversal is JOB-SCOPED: email_organize_log rows written by a cleanup job carry that
-- job's id in the new cleanup_job_id column (NULL = written by the standing LUM-645
-- organize), so undoing a cleanup job never reverses a row the standing organize owns.

CREATE TABLE email_cleanup_job (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id              TEXT NOT NULL DEFAULT 'default',
    source_id            TEXT NOT NULL,
    kind                 TEXT NOT NULL DEFAULT 'window',  -- 'window' | 'spam'
    window_start         DATE,                 -- inclusive lower bound; NULL = unbounded (kind='window')
    window_end           DATE,                 -- inclusive upper bound; NULL = today   (kind='window')
    state                TEXT NOT NULL DEFAULT 'pending',  -- pending|backfilling|ready|executing|undoing|done|failed
    backfill_cursor      BIGINT NOT NULL DEFAULT 0,   -- window: INBOX UID walk; spam: \Junk UID walk
    backfill_max_uid     BIGINT NOT NULL DEFAULT 0,
    backfill_uidvalidity BIGINT,
    approved_actions     JSONB NOT NULL DEFAULT '[]'::jsonb,  -- set by approve(); consumed by the tick
    undo_ops             TEXT[],               -- non-NULL => a job-scoped undo sweep is pending
    undo_ticks           INTEGER NOT NULL DEFAULT 0,   -- bound the undoing state (see _UNDO_MAX_TICKS)
    scanned              INTEGER NOT NULL DEFAULT 0,
    classified           INTEGER NOT NULL DEFAULT 0,
    labeled              INTEGER NOT NULL DEFAULT 0,
    executed             INTEGER NOT NULL DEFAULT 0,   -- move/trash ledger rows written
    reversed             INTEGER NOT NULL DEFAULT 0,   -- rows reversed by undo
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_email_cleanup_job_source ON email_cleanup_job (source_id, state);

-- At most one active (non-terminal) cleanup job per source: window and spam jobs are
-- mutually exclusive (start_job / start_spam_job -> 409 cleanup_active otherwise).
CREATE UNIQUE INDEX uq_email_cleanup_active ON email_cleanup_job (source_id)
    WHERE state IN ('pending', 'backfilling', 'ready', 'executing', 'undoing');

CREATE TABLE email_cleanup_item (
    job_id      UUID NOT NULL REFERENCES email_cleanup_job(id) ON DELETE CASCADE,
    signal_url  TEXT NOT NULL,   -- imap://{source_id}/{uidvalidity}/{uid}; SYNTHETIC for kind='spam'
                                 -- (\Junk messages are not signals, but email_organize_log.signal_url
                                 -- is plain TEXT with no FK, so a synthetic key is safe).
    category    TEXT NOT NULL DEFAULT '',   -- taxonomy slug, or '__spam__' for kind='spam' items
    PRIMARY KEY (job_id, signal_url)
);

CREATE INDEX idx_email_cleanup_item_cat ON email_cleanup_item (job_id, category);

-- Attribute ledger rows to the cleanup job that wrote them, so job-scoped undo reverses
-- ONLY this job's rows -- never a row the standing organize owns (both features share
-- email_organize_log; a signal in both scopes shares one active row).
ALTER TABLE email_organize_log ADD COLUMN cleanup_job_id UUID;  -- NULL = written by standing organize

CREATE INDEX idx_email_organize_log_cleanup_job
    ON email_organize_log (cleanup_job_id) WHERE cleanup_job_id IS NOT NULL;
