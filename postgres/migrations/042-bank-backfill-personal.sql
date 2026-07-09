-- Migration 042: Reassign legacy pre-MCP household rows from `coding` → `personal` (LUM-293).
--
-- PREREQUISITE: deploy code that stamps metadata.source='mcp' on MCP writes, then
-- optionally run a one-shot provenance stamp for existing MCP rows:
--   UPDATE memories SET metadata = metadata || '{"source":"mcp"}'::jsonb
--     WHERE COALESCE(metadata->>'source','') <> 'mcp';
-- Idempotent; forward-only data backfill (no DDL).

UPDATE memories
   SET bank = 'personal'
 WHERE bank = 'coding'
   AND COALESCE(metadata->>'source', '') <> 'mcp';

UPDATE entity_edges ee
   SET bank = 'personal'
 WHERE ee.bank = 'coding'
   AND ee.evidence_id IS NOT NULL
   AND NOT EXISTS (
     SELECT 1 FROM memories m
      WHERE m.id = ee.evidence_id
        AND m.user_id = ee.user_id
        AND COALESCE(m.metadata->>'source', '') = 'mcp'
   );
