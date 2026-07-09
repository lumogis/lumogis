-- 046: allow shared/system projections of file_index rows (LUM-157).
--
-- Migration 011 created a FULL unique index on (user_id, file_path):
--     file_index_user_path_uniq ON file_index (user_id, file_path)
-- Household sharing (LUM-157) projects a personal document to scope='shared'
-- by inserting a second file_index row with the SAME user_id + file_path
-- (published_from = source id, scope='shared'). That collides with the full
-- index -> UniqueViolation on the first share.
--
-- Fix: make the uniqueness constraint PARTIAL so it applies only to source
-- rows (published_from IS NULL). Shared/system projection rows are exempt and
-- remain deduplicated by file_index_published_from_scope_uniq (migration 013,
-- also partial). This mirrors how migration 013 scoped its projection index.
--
-- The read-side (user_id, file_path) lookups migration 011 relied on are still
-- satisfied: the partial index still covers the common personal-source case,
-- and a non-unique btree keeps projection-row lookups indexed.

DROP INDEX IF EXISTS file_index_user_path_uniq;

CREATE UNIQUE INDEX IF NOT EXISTS file_index_user_path_uniq
    ON file_index (user_id, file_path)
    WHERE published_from IS NULL;

-- Keep (user_id, file_path) read lookups indexed for projection rows too.
CREATE INDEX IF NOT EXISTS file_index_user_path_idx
    ON file_index (user_id, file_path);

-- Down (manual; only safe once all shared/system projections are purged, else
-- the full unique index build fails on duplicate (user_id, file_path)):
--   DROP INDEX IF EXISTS file_index_user_path_idx;
--   DROP INDEX IF EXISTS file_index_user_path_uniq;
--   CREATE UNIQUE INDEX file_index_user_path_uniq ON file_index (user_id, file_path);
