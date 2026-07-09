-- Migration 039: MCP memory write surface — `entity_edges` table (LUM-291).
--
-- Postgres is the system-of-record for typed inter-entity relations written
-- via the MCP `add_relation` tool (and extracted by `add_memory`). The
-- pre-existing `entity_relations` table is provenance-only (no target_id, no
-- bank); `entity_edges` is a directed, typed, bank-scoped edge between two
-- `entities.entity_id` UUIDs. When a graph backend is enabled
-- (GRAPH_BACKEND=falkordb) the writer projects these to FalkorDB RELATES_TO;
-- with the default GRAPH_BACKEND=none the Postgres write is sufficient (no
-- relations are lost on the default install).
--
-- No FK on src/dst by design (pragmatic MVP); the service layer guarantees
-- both endpoints are real entity ids before insert. The UNIQUE index makes
-- a repeated add_relation idempotent. `valid_from`/`valid_until` mirror the
-- memories table for the deferred forget/update_observation tools.
-- Idempotent (IF NOT EXISTS); additive — safe on existing volumes.

CREATE TABLE IF NOT EXISTS entity_edges (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL DEFAULT 'default',
    bank           TEXT NOT NULL DEFAULT 'coding',
    src_entity_id  TEXT NOT NULL,
    dst_entity_id  TEXT NOT NULL,
    relation_type  TEXT NOT NULL,
    evidence_id    TEXT,
    valid_from     TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_until    TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, bank, src_entity_id, dst_entity_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_entity_edges_src ON entity_edges (user_id, bank, src_entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_edges_dst ON entity_edges (user_id, bank, dst_entity_id);
