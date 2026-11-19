-- Migration 084: delete an entry, names Lumogis won't learn, Hide (LUM-966).
--
-- entity_name_rules gains action 'block' (extraction drops the name at that kind
-- for that member). A delete blocks the entry's names at its kind and converts
-- each retype leading to that kind into a block on its source kind, keeping the
-- retype's target in restore_to_type, so one lookup still decides (no retype
-- points at a blocked kind). document_evidence holds the entry's document
-- evidence ids (file paths) at delete time, for the re-read offer.
-- entity_hidden: a member's Hide of another member's shared entry, keyed by the
-- owner's source row (cascades when the owner deletes it).
-- entity_deletions: ids-only retry tombstones for the Qdrant and graph arms.
-- Rollback (loses the won't-learn list and hides; deletes stay done):
--   UPDATE entity_name_rules SET action = 'retype', to_type = restore_to_type,
--       restore_to_type = NULL WHERE action = 'block' AND restore_to_type IS NOT NULL;
--   DELETE FROM entity_name_rules WHERE action = 'block';
--   ALTER TABLE entity_name_rules DROP CONSTRAINT entity_name_rules_evidence_cap,
--       DROP CONSTRAINT entity_name_rules_restore, DROP COLUMN document_evidence,
--       DROP COLUMN display_name, DROP COLUMN restore_to_type;
--   ALTER TABLE entity_name_rules DROP CONSTRAINT entity_name_rules_action;
--   ALTER TABLE entity_name_rules ADD CONSTRAINT entity_name_rules_action
--       CHECK (action IN ('retype'));
--   DROP TABLE entity_hidden, entity_deletions;

ALTER TABLE entity_name_rules DROP CONSTRAINT IF EXISTS entity_name_rules_action;
ALTER TABLE entity_name_rules
    ADD CONSTRAINT entity_name_rules_action CHECK (action IN ('retype', 'block'));
ALTER TABLE entity_name_rules ADD COLUMN IF NOT EXISTS restore_to_type   TEXT;
ALTER TABLE entity_name_rules ADD COLUMN IF NOT EXISTS display_name      TEXT;
ALTER TABLE entity_name_rules ADD COLUMN IF NOT EXISTS document_evidence TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE entity_name_rules DROP CONSTRAINT IF EXISTS entity_name_rules_restore;
ALTER TABLE entity_name_rules ADD CONSTRAINT entity_name_rules_restore
    CHECK ((action = 'block' OR restore_to_type IS NULL)
           AND (restore_to_type IS NULL OR restore_to_type <> from_type));
ALTER TABLE entity_name_rules DROP CONSTRAINT IF EXISTS entity_name_rules_evidence_cap;
ALTER TABLE entity_name_rules ADD CONSTRAINT entity_name_rules_evidence_cap
    CHECK (cardinality(document_evidence) <= 500);
-- entity_name_rules_retype_target and entity_name_rules_no_self are unchanged:
-- a block has to_type NULL.

CREATE TABLE IF NOT EXISTS entity_hidden (
    user_id    TEXT        NOT NULL DEFAULT 'default',
    entity_key UUID        NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    hidden_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, entity_key)
);

CREATE TABLE IF NOT EXISTS entity_deletions (
    entity_id         UUID        PRIMARY KEY,
    user_id           TEXT        NOT NULL DEFAULT 'default',
    projection_ids    UUID[]      NOT NULL DEFAULT '{}',
    deleted_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    points_cleared_at TIMESTAMPTZ,
    graph_cleared_at  TIMESTAMPTZ,
    attempts          SMALLINT    NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS entity_deletions_pending_idx
    ON entity_deletions (deleted_at)
    WHERE points_cleared_at IS NULL OR graph_cleared_at IS NULL;
