-- Migration 006: KG Quality Pipeline Pass 3 — edge_scores table
-- Applies to existing installations; postgres/init.sql mirrors this for greenfield.
-- edge_scores is NOT in the backup table list — it is recomputable from entity_relations
-- by the weekly quality maintenance job.

CREATE TABLE IF NOT EXISTS edge_scores (
    id                BIGSERIAL PRIMARY KEY,
    user_id           TEXT NOT NULL DEFAULT 'default',
    entity_id_a       UUID NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    entity_id_b       UUID NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    ppmi_score        DOUBLE PRECISION,
    edge_quality      DOUBLE PRECISION,
    decay_factor      DOUBLE PRECISION,
    last_evidence_at  TIMESTAMPTZ,
    computed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, entity_id_a, entity_id_b),
    CHECK (entity_id_a < entity_id_b)
);

CREATE INDEX IF NOT EXISTS idx_edge_scores_user ON edge_scores (user_id);
