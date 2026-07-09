-- Migration 033: add entity GC columns to purged_documents (LUM-501).
-- Tracks orphan-entity Qdrant arm state so retry can skip already-succeeded arms.

BEGIN;

ALTER TABLE purged_documents
    ADD COLUMN IF NOT EXISTS qdrant_entities_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS orphan_entity_ids        JSONB   NOT NULL DEFAULT '[]';

COMMIT;
