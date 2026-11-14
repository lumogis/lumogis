-- Migration 053: email signal category (LUM-170).
--
-- Adds the classification category label to signals. Empty-string default
-- keeps every existing row valid and needs no backfill (constant default is
-- metadata-only on Postgres >= 11). Populated only for email ("imap") signals
-- in v1; other source types leave it "".
--
-- Idempotent / re-runnable: ADD COLUMN IF NOT EXISTS.

BEGIN;

ALTER TABLE signals ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT '';

COMMENT ON COLUMN signals.category IS
  'LUM-170 email classification label (one of the fixed EMAIL_CATEGORIES for '
  'imap signals; empty for other source types).';

COMMIT;
