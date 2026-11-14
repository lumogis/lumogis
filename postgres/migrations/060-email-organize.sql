-- LUM-645 (Label write-back): per-source, opt-in mailbox organization + reversal ledger.
-- All opt-in columns default OFF/NULL so every existing email source behaves exactly
-- as before (no mailbox writes without explicit consent).
--
-- Two opt-ins (independent, per-mailbox):
--   organize_label_enabled   -- tag every classified email with its category (Gmail
--                               label / IMAP keyword). Non-destructive; stays in inbox.
--   organize_folder_enabled  -- move SELECTED categories into per-category folders.
--                               organize_folder_categories = JSONB list of slugs; 'other'
--                               is never stored (protected catch-all is never moved).
--
-- Reconcile is a STANDING per-tick sweep (not a one-shot driver): the ledger itself is
-- the progress marker (a per-op LEFT JOIN excludes done work, includes stragglers), so a
-- transient go-forward failure (no ledger row) is retried on the next tick.
--   organize_since       -- watermark: NOW() when an opt-in is first enabled (go-forward
--                           only); apply-to-existing moves it to epoch to widen the sweep
--                           over history. NULL = never opted in.
--   organize_undo_scope  -- non-NULL ('label'|'folder'|'all') => an undo sweep is pending
--                           (tick-driven, bounded); NULL = idle.
ALTER TABLE sources
    ADD COLUMN IF NOT EXISTS organize_label_enabled     BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS organize_folder_enabled    BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS organize_folder_categories JSONB,
    ADD COLUMN IF NOT EXISTS organize_since             TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS organize_undo_scope        TEXT;

-- Write-audit ledger: idempotency + bulk reversal + activity log. Keyed on the signal
-- URL (imap://{source_id}/{uidvalidity}/{uid}) — email signals share signal_id semantics
-- via the deterministic uuid5(url) id (LUM-676), but the URL is the natural per-message
-- organization key and what apply_writeback parses (uidvalidity/uid).
CREATE TABLE IF NOT EXISTS email_organize_log (
    id            BIGSERIAL PRIMARY KEY,
    user_id       TEXT NOT NULL DEFAULT 'default',
    source_id     UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    signal_url    TEXT NOT NULL,            -- imap://{source_id}/{uidvalidity}/{uid}
    op            TEXT NOT NULL,            -- 'label' | 'move'
    target        TEXT,                     -- exact artifact written (human label / folder / keyword atom)
    label_kind    TEXT,                     -- 'gmail' | 'keyword' | NULL (move)
    reverse_token JSONB NOT NULL,           -- handler-shaped {source_id, **reverse} for undo
    applied_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reversed_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_email_organize_log_source ON email_organize_log (source_id);
CREATE INDEX IF NOT EXISTS idx_email_organize_log_user   ON email_organize_log (user_id);
-- Idempotency: at most one ACTIVE (un-reversed) row per (signal_url, op).
CREATE UNIQUE INDEX IF NOT EXISTS uq_email_organize_active
    ON email_organize_log (signal_url, op) WHERE reversed_at IS NULL;
-- reconcile_tick scans signals by source_id; no such index existed before.
CREATE INDEX IF NOT EXISTS idx_signals_source_id ON signals (source_id);

-- Rollback (manual DDL; the migration runner is apply-only / filename-tracked):
-- DROP INDEX IF EXISTS idx_signals_source_id;
-- DROP TABLE IF EXISTS email_organize_log;
-- ALTER TABLE sources
--     DROP COLUMN organize_label_enabled,
--     DROP COLUMN organize_folder_enabled,
--     DROP COLUMN organize_folder_categories,
--     DROP COLUMN organize_since,
--     DROP COLUMN organize_undo_scope;
