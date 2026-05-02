-- Migration 004: KG quality fields — extraction_quality, is_staged, evidence_granularity
-- Applies to existing installations; postgres/init.sql mirrors these columns for greenfield.

-- Quality score in [0, 1] assigned by the heuristic scorer at extraction time.
-- NULL means the entity was inserted before Pass 1 was deployed.
ALTER TABLE entities ADD COLUMN IF NOT EXISTS extraction_quality DOUBLE PRECISION;

-- Staged entities exist in Postgres but are excluded from FalkorDB projection,
-- co-occurrence edges, and all graph queries until promoted.
ALTER TABLE entities ADD COLUMN IF NOT EXISTS is_staged BOOLEAN NOT NULL DEFAULT FALSE;

-- Partial index: fast lookup of staged entities per user (reconcile + promotion queries).
CREATE INDEX IF NOT EXISTS idx_entities_staged ON entities (user_id, is_staged) WHERE is_staged = TRUE;

-- Granularity of co-occurrence evidence stored on the provenance edge.
-- Allowed values: 'sentence' | 'paragraph' | 'document'
-- Default 'document' backfills existing rows correctly.
ALTER TABLE entity_relations ADD COLUMN IF NOT EXISTS evidence_granularity TEXT NOT NULL DEFAULT 'document';
