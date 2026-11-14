-- SPDX-License-Identifier: AGPL-3.0-only
-- Copyright (C) 2026 Lumogis
--
-- LUM-819 — email search over subject, sender and MESSAGE TEXT, locally.
--
-- `extract_text` already runs at ingest and its output is discarded
-- (`adapters/email_imap_source.py`: "fetch_bodies calls _body_to_text(raw) and
-- discards raw"). This stops discarding it. Storage is a separate table, not a
-- column on `signals`, so the hot triage-lane queries are untouched, retention
-- and erasure are independent, opting out is simply *no row*, and non-email
-- sources are not carrying a NULL.
--
-- NO explicit BEGIN/COMMIT: db_migrations.py runs each file inside one
-- transaction and commits it TOGETHER WITH the schema_migrations row. An
-- explicit COMMIT here would end that transaction early and decouple "applied"
-- from "recorded", so the migration would re-run on every boot.
--
-- Rollback (manual; the runner is apply-only / filename-tracked):
--   DROP TABLE IF EXISTS email_body_text_dead;
--   DROP TABLE IF EXISTS email_body_text;
--   -- the extension is deliberately left in place; dropping it would break
--   -- anything else that later adopts it.

-- The repo's first CREATE EXTENSION. Contrib, not third-party, and the official
-- Postgres image runs POSTGRES_USER as a superuser with contrib bundled.
--
-- LUM-828 -- WHO ACTUALLY FAILS HERE, measured on Postgres 16.13 rather than
-- assumed. `pg_trgm` is a TRUSTED extension (PG13+; `pg_available_extension_
-- versions.trusted = t`), so superuser is NOT the requirement. Three cases:
--
--   1. Non-superuser that OWNS the database        -> SUCCEEDS (trusted).
--      This is the shape managed Postgres normally hands you, so RDS /
--      Cloud SQL / Supabase are not blocked by this line.
--   2. Non-superuser with neither ownership nor CREATE on the database
--                                                  -> FAILS. The only real
--      failure case, and a deliberately locked-down one.
--   3. Any role at all, once an administrator has installed the extension
--      -> SUCCEEDS via IF NOT EXISTS. This is the documented pre-step, and
--      it is verified, not hoped for.
--
-- Case 2 stays FAIL-CLOSED on purpose. `db_migrations.main` returns 2 on the
-- boot path, so the orchestrator does not start -- which is correct: a partial
-- schema is worse than a refusal. Degrading to a trigram-less schema was
-- considered and rejected (ADR-211 § Consequences): it would mean two schema
-- shapes to reason about forever, and an unindexed ILIKE that is silently
-- slower, to serve a configuration nobody has asked for.
--
-- What the DO block buys is the difference between a bare `permission denied
-- to create extension "pg_trgm"` in a crash-looping container and a message
-- naming the remedy. The bare error is not wrong, it is just not actionable
-- at 2am.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
EXCEPTION
    WHEN insufficient_privilege THEN
        RAISE EXCEPTION
            'migration 074 (LUM-819 email search) requires the pg_trgm extension, '
            'and this role may not create it'
            USING
                DETAIL =
                    'pg_trgm is a trusted extension, so superuser is not required: '
                    'a role that owns the database, or holds CREATE on it, can '
                    'install it. This role has neither.',
                HINT =
                    'Run once as an administrator against this database: '
                    'CREATE EXTENSION pg_trgm;  -- then restart the orchestrator. '
                    'Alternatively grant the app role CREATE on the database.';
END
$$;

CREATE TABLE IF NOT EXISTS email_body_text (
    signal_id   UUID PRIMARY KEY REFERENCES signals(signal_id) ON DELETE CASCADE,
    user_id     TEXT        NOT NULL DEFAULT 'default',
    text        TEXT        NOT NULL,
    truncated   BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- STORED generated column, exactly as migration 041 did for `memories`,
    -- and on the same 'english' config it uses. NOT an expression index: a
    -- stored column cannot drift from the query expression, and ts_rank_cd
    -- reads it instead of recomputing to_tsvector per candidate row at rank
    -- time.
    --
    -- The TWO-ARGUMENT to_tsvector is load-bearing: to_tsvector(regconfig,text)
    -- is IMMUTABLE, while the one-argument form is only STABLE (it reads
    -- default_text_search_config) and Postgres refuses it in a generated
    -- column with "generation expression is not immutable".
    --
    -- 'english' is safe for German mail ONLY because every query token carries
    -- the `:*` prefix operator -- measured, `Rechnung:*` matches "rechnungen"
    -- that plain 'english' misses. See services/email_search.py.
    text_tsv    tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
);

CREATE INDEX IF NOT EXISTS email_body_text_tsv_idx
    ON email_body_text USING GIN (text_tsv);

CREATE INDEX IF NOT EXISTS email_body_text_user_idx
    ON email_body_text (user_id);

-- Permanently-unfetchable backfill candidates. Without this the backfill
-- re-selects its own failures on every run; once they outnumber --limit it
-- scans `limit` dead rows, stores nothing, and reports cheerful counts
-- forever. This table is what makes the job terminate.
CREATE TABLE IF NOT EXISTS email_body_text_dead (
    signal_id  UUID PRIMARY KEY REFERENCES signals(signal_id) ON DELETE CASCADE,
    reason     TEXT        NOT NULL,
    attempts   INTEGER     NOT NULL DEFAULT 1,
    last_try   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Short, identifier-like, typo-prone, substring-matched fields: trigram.
-- `signals` has no index on any of these today.
CREATE INDEX IF NOT EXISTS signals_title_trgm_idx
    ON signals USING GIN (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS signals_from_addr_trgm_idx
    ON signals USING GIN (from_addr gin_trgm_ops);
CREATE INDEX IF NOT EXISTS signals_from_display_trgm_idx
    ON signals USING GIN (from_display gin_trgm_ops);
CREATE INDEX IF NOT EXISTS signals_summary_trgm_idx
    ON signals USING GIN (content_summary gin_trgm_ops);
