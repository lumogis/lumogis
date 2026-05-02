-- Migration 012: dedup contract for entity_relations.
-- Background: per_user_file_index_and_ingest_attribution (011) introduced the
-- ON CONFLICT idiom across the project; entity_relations was carved out as a
-- soft-guard exit because the cleanup needed a real migration. See
-- .cursor/adrs/entity_relations_evidence_dedup.md and ADR (finalised
-- by /verify-plan into docs/decisions/NNN-entity-relations-evidence-dedup.md).
--
-- Decision: UNIQUE(source_id, evidence_id, relation_type, user_id).
-- evidence_granularity is intentionally NOT in the tuple (granularity is a
-- property of the link, not a key dimension; future paragraph-level extractor
-- → sibling overrides table, not relaxed UNIQUE).
--
-- Greenfield: ADD CONSTRAINT directly. No pre-cleanup.

CREATE UNIQUE INDEX IF NOT EXISTS entity_relations_evidence_uniq
    ON entity_relations (source_id, evidence_id, relation_type, user_id);
-- One index for both uniqueness and read-side per-evidence/per-source lookups.
-- Postgres satisfies any leading-prefix read (source_id alone, source_id +
-- evidence_id, etc.) from this unique index; no separate non-unique helper
-- index is needed.
