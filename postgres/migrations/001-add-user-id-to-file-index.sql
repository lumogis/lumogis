-- Migration 001: add user_id to file_index
-- Applies to existing installations where the database was created before
-- user_id was added to init.sql. Fresh docker compose up from scratch does
-- not need this — init.sql already includes user_id.
--
-- Apply with:
--   docker compose exec postgres psql -U lumogis -d lumogis -f /migrations/001-add-user-id-to-file-index.sql
-- Or directly:
--   docker compose exec postgres psql -U lumogis -d lumogis -c \
--     "ALTER TABLE file_index ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'default';"

ALTER TABLE file_index ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'default';
