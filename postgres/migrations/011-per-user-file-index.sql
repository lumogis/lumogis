-- Migration 011: per-user file_index uniqueness.
--
-- Audit B12 (docs/private/MULTI-USER-AUDIT.md §12 Phase B) — the bare-path
-- UNIQUE on file_index.file_path means the second user to ingest the same
-- path collides with the first user's row. Replace with composite
-- UNIQUE(user_id, file_path) so two users can independently ingest the
-- same absolute path without conflict.
--
-- Coupled with audit B11 (per-user namespacing of deterministic Qdrant
-- point ids) — this migration only fixes the Postgres side; the Python
-- code change to services.point_ids ships in the same PR.
--
-- Idempotent. Re-runnable. Greenfield-safe (no row backfill required —
-- user_id was added to file_index in migration 001-…; every existing
-- row already has a user_id value).

ALTER TABLE file_index
    DROP CONSTRAINT IF EXISTS file_index_file_path_key;

CREATE UNIQUE INDEX IF NOT EXISTS file_index_user_path_uniq
    ON file_index (user_id, file_path);
-- One index for both uniqueness and read-side `(user_id, file_path)` lookups.
-- Postgres satisfies any read query against `(user_id, file_path)` from this
-- unique index; no separate non-unique helper index is needed (and adding
-- one would only inflate writes for zero query-plan benefit).
