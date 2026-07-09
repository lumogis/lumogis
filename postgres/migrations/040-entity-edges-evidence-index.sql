-- Migration 040: index entity_edges.evidence_id for the MCP archive path (LUM-526).
--
-- `archive_edges_for_memory` (forget / update_observation) runs
--   UPDATE entity_edges SET valid_until = now()
--   WHERE evidence_id = %s AND user_id = %s AND valid_until IS NULL
-- Migration 039 indexed only src/dst, so that filter would full-scan
-- entity_edges. This index serves it. Index-only, additive, no backfill.

CREATE INDEX IF NOT EXISTS idx_entity_edges_evidence ON entity_edges (user_id, evidence_id);
