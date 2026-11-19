-- Migration 083: undo records for entity merges (LUM-965).
--
-- One row per merge (member, review queue, admin or automatic dedup), written in
-- the same transaction as the merge. It names, by id, every row the merge moved
-- or dropped, so ``services/entity_merge.undo_in_transaction`` can replay it in
-- reverse. Merges made before this migration have no record and cannot be undone.
--
-- Retention: there is no expiry in v1. ``loser_row`` and the dropped-row JSONB
-- hold a copy of data the member merged away. A retention rule waits for LUM-966
-- (delete), which owns the privacy question; until then the FK cascade below is
-- the only thing that removes a record (the survivor is deleted).
--
-- ``loser_row`` snapshot drift: undo re-inserts it with ``jsonb_populate_record``,
-- so a column added to ``entities`` later restores as its default (or NULL). Any
-- later migration that adds a NOT NULL column without a default to ``entities``
-- MUST backfill ``entity_merges.loser_row`` (and the dropped-row JSONB) for it.
--
-- Rollback: ``DROP TABLE IF EXISTS entity_merges;`` loses only the ability to
-- undo; the merges stay applied.

CREATE TABLE IF NOT EXISTS entity_merges (
    merge_id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id              TEXT        NOT NULL DEFAULT 'default',
    -- Cascade: deleting the survivor (LUM-966 delete, user delete) removes the
    -- record and the loser snapshot inside it. A later merge of the survivor
    -- re-points this column first (records_remapped), so the record survives.
    winner_id            UUID        NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    loser_id             UUID        NOT NULL,
    origin               TEXT        NOT NULL,
    acted_by             TEXT,
    keep_name            TEXT        NOT NULL DEFAULT 'winner',
    loser_row            JSONB       NOT NULL,
    record_version       SMALLINT    NOT NULL DEFAULT 1,
    winner_before        JSONB       NOT NULL,
    name_set             TEXT,
    aliases_added        TEXT[]      NOT NULL DEFAULT '{}',
    tags_added           TEXT[]      NOT NULL DEFAULT '{}',
    relations_moved      INTEGER[]   NOT NULL DEFAULT '{}',
    relations_dropped    JSONB       NOT NULL DEFAULT '[]',
    sessions             JSONB       NOT NULL DEFAULT '[]',
    edges_moved_src      TEXT[]      NOT NULL DEFAULT '{}',
    edges_moved_dst      TEXT[]      NOT NULL DEFAULT '{}',
    edges_archived       TEXT[]      NOT NULL DEFAULT '{}',
    edges_dropped        JSONB       NOT NULL DEFAULT '[]',
    projections_moved    UUID[]      NOT NULL DEFAULT '{}',
    projections_dropped  JSONB       NOT NULL DEFAULT '[]',
    distinct_pairs_added JSONB       NOT NULL DEFAULT '[]',
    distinct_pairs_lost  JSONB       NOT NULL DEFAULT '[]',
    name_rules           JSONB,
    records_remapped     UUID[]      NOT NULL DEFAULT '{}',
    merged_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    undone_at            TIMESTAMPTZ,
    undone_by            TEXT,
    CONSTRAINT entity_merges_origin CHECK (origin IN ('member','auto_dedup','review_queue','admin')),
    CONSTRAINT entity_merges_keep_name CHECK (keep_name IN ('winner','loser')),
    CONSTRAINT entity_merges_distinct CHECK (winner_id <> loser_id)
);
CREATE INDEX IF NOT EXISTS entity_merges_user_winner_idx
    ON entity_merges (user_id, winner_id, merged_at DESC);
CREATE INDEX IF NOT EXISTS entity_merges_user_loser_idx
    ON entity_merges (user_id, loser_id);
