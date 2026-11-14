-- Migration 081: index entity_relations by evidence (LUM-952).
--
-- The graph writer computes RELATES_TO co_occurrence_count as the number of
-- distinct evidence items two entities share (services/lumogis-graph/graph/
-- writer.py::_update_cooccurrence_edges). The aggregate self-joins
-- entity_relations on evidence_id; the only existing index,
-- entity_relations_evidence_uniq, leads with source_id, so the evidence side of
-- that join (and the single-evidence sibling filter it replaced) scanned the
-- table. (evidence_id, user_id) serves both, and every join is user-scoped.
--
-- Idempotent; plain CREATE INDEX (not CONCURRENTLY) because migrations run in
-- a transaction on a single-household table.

CREATE INDEX IF NOT EXISTS entity_relations_evidence_user_idx
    ON entity_relations (evidence_id, user_id);
