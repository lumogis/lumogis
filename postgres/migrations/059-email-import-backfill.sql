-- LUM-644 (Email Setup v2): per-source import scope + additive historical backfill.
-- All columns nullable / no default so every existing email source (all NULL)
-- behaves exactly as before: NULL import_state => the driver skips seeding and the
-- legacy NULL poll_cursor 1:* drain is unchanged.
--
-- Two independent cursors, never conflated:
--   poll_cursor (existing)          -- live forward tracking {uidvalidity}:{last_uid}
--   backfill_cursor + backfill_max_uid (new) -- historical backfill; additive, never
--                                            -- writes poll_cursor.
-- backfill_max_uid is the seed-time live-cursor UID (UIDNEXT-1); it is the SEARCH
-- upper bound only. Completion is an EMPTY search result (no UID in
-- (backfill_cursor, backfill_max_uid] matching SINCE [BEFORE]) -- NOT
-- backfill_cursor >= backfill_max_uid (which never terminates when out-of-window
-- mail sits above the window).
ALTER TABLE sources
    ADD COLUMN IF NOT EXISTS import_scope         TEXT,    -- just_new|last_7d|last_30d|custom (NULL = legacy)
    ADD COLUMN IF NOT EXISTS import_since         DATE,    -- date/custom scopes: SINCE lower bound
    ADD COLUMN IF NOT EXISTS import_before        DATE,    -- custom end (optional; NULL = up to seed time)
    ADD COLUMN IF NOT EXISTS import_state         TEXT,    -- pending|backfilling|done (NULL = legacy)
    ADD COLUMN IF NOT EXISTS backfill_cursor      BIGINT,  -- highest UID already backfilled
    ADD COLUMN IF NOT EXISTS backfill_max_uid     BIGINT,  -- seed-time UIDNEXT-1; search upper bound
    ADD COLUMN IF NOT EXISTS backfill_uidvalidity BIGINT;  -- UIDVALIDITY backfill_cursor/max_uid are valid under

-- Rollback (manual DDL; the migration runner is apply-only / filename-tracked):
-- ALTER TABLE sources
--     DROP COLUMN import_scope,
--     DROP COLUMN import_since,
--     DROP COLUMN import_before,
--     DROP COLUMN import_state,
--     DROP COLUMN backfill_cursor,
--     DROP COLUMN backfill_max_uid,
--     DROP COLUMN backfill_uidvalidity;
