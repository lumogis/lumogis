-- Migration 005: KG quality constraints — constraint_violations table
-- Applies to existing installations; postgres/init.sql mirrors this for greenfield.

CREATE TABLE IF NOT EXISTS constraint_violations (
    violation_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         TEXT NOT NULL DEFAULT 'default',
    entity_id       UUID REFERENCES entities(entity_id) ON DELETE CASCADE,
    rule_name       TEXT NOT NULL,
    severity        TEXT NOT NULL CHECK (severity IN ('CRITICAL', 'WARNING', 'INFO')),
    detail          TEXT,
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_constraint_violations_open
    ON constraint_violations (user_id, severity, detected_at DESC)
    WHERE resolved_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_constraint_violations_entity
    ON constraint_violations (entity_id)
    WHERE resolved_at IS NULL;
