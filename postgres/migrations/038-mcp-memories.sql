-- Migration 038: MCP memory write surface — `memories` table (LUM-291).
--
-- The atomic "observation" record persisted by the MCP `add_memory` write
-- tool so Cursor / Claude Code can use Lumogis as a memory backend. Entities
-- and typed relations are extracted from `content` and stored separately
-- (entities table + entity_edges, migration 039); this table is the
-- system-of-record for the raw memory text.
--
-- `valid_from` / `valid_until` are bitemporal-ready from day one: the MVP
-- always writes `valid_until = NULL` (currently valid); the deferred
-- `forget` / `update_observation` tools (LUM-291 follow-up) set `valid_until`
-- to supersede without deleting. `bank` isolates memories per context
-- (coding / personal); real FalkorDB multi-graph isolation is LUM-293.
-- Idempotent (IF NOT EXISTS); additive — safe on existing volumes.

CREATE TABLE IF NOT EXISTS memories (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL DEFAULT 'default',
    bank         TEXT NOT NULL DEFAULT 'coding',
    content      TEXT NOT NULL,
    tags         TEXT[] NOT NULL DEFAULT '{}',
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
    valid_from   TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_until  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_memories_user_bank ON memories (user_id, bank);
CREATE INDEX IF NOT EXISTS idx_memories_valid ON memories (user_id, bank, valid_until);
