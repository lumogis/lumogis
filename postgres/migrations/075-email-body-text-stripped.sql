-- SPDX-License-Identifier: AGPL-3.0-only
-- Copyright (C) 2026 Lumogis
--
-- LUM-826 — index the message, not the thread.
--
-- `extract_text` returns quoted reply history along with the new content, and
-- migration 074 stored that verbatim. A sentence written once in the first
-- message of a thread is therefore indexed again in every reply that quotes
-- it: ~8.6x storage amplification, and one phrase returning five hits for a
-- single conversation.
--
-- This adds the STRIPPED copy beside the original and re-points the search
-- vector at it. `text` is deliberately left untouched:
--
--   * quote stripping is a heuristic, and one that destroys the original is
--     unfixable — keeping both means a better stripper can simply be re-run;
--   * `_snippet` needs *some* text to window over, and falling back to the
--     full message is strictly better than an empty snippet.
--
-- COALESCE is the whole contract. `text_stripped IS NULL` means "the stripper
-- declined" — no markers found, or stripping left nothing substantive — and
-- the original is indexed instead. A message that is ENTIRELY a quoted block
-- therefore stays findable rather than becoming an empty index entry. See
-- services/email_quote_strip.py, which is the only writer of this column.
--
-- NO explicit BEGIN/COMMIT: db_migrations.py runs each file inside one
-- transaction and commits it together with the schema_migrations row.
--
-- COST: dropping and re-adding a STORED generated column rewrites the table
-- and rebuilds the GIN index. At household scale (tens of thousands of rows)
-- that is seconds. It is not an online migration and is not claimed to be.
--
-- Existing rows keep `text_stripped IS NULL` until backfilled, so search
-- behaviour is UNCHANGED for them rather than silently half-migrated:
--   python -m services.email_body_backfill --restrip [--limit N]
--
-- Rollback (manual; the runner is apply-only / filename-tracked):
--   ALTER TABLE email_body_text DROP COLUMN text_tsv;
--   ALTER TABLE email_body_text
--       ADD COLUMN text_tsv tsvector
--       GENERATED ALWAYS AS (to_tsvector('english', text)) STORED;
--   CREATE INDEX email_body_text_tsv_idx ON email_body_text USING GIN (text_tsv);
--   ALTER TABLE email_body_text DROP COLUMN text_stripped;

ALTER TABLE email_body_text
    ADD COLUMN IF NOT EXISTS text_stripped TEXT;

COMMENT ON COLUMN email_body_text.text_stripped IS
    'Quoted reply history removed (LUM-826). NULL = stripper declined; index the '
    'full `text` instead. Written only by services/email_quote_strip.py.';

-- The generated expression cannot be altered in place, so the column and its
-- index are replaced. Dropping the column drops the index with it; it is
-- recreated below under the same name.
ALTER TABLE email_body_text
    DROP COLUMN IF EXISTS text_tsv;

-- Two-argument to_tsvector remains load-bearing: to_tsvector(regconfig, text)
-- is IMMUTABLE, the one-argument form only STABLE, and Postgres refuses a
-- non-immutable expression in a generated column.
ALTER TABLE email_body_text
    ADD COLUMN text_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('english', COALESCE(text_stripped, text))) STORED;

CREATE INDEX IF NOT EXISTS email_body_text_tsv_idx
    ON email_body_text USING GIN (text_tsv);
