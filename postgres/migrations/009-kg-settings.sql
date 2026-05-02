-- Migration 009: Hot-reload KG settings table
-- Stores tunable KG quality and graph parameters that take effect immediately
-- without a container restart.  Config getters check this table first and fall
-- back to env vars.  Env vars remain the canonical default and fallback.

CREATE TABLE IF NOT EXISTS kg_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION update_kg_settings_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER kg_settings_updated_at
    BEFORE UPDATE ON kg_settings
    FOR EACH ROW EXECUTE FUNCTION update_kg_settings_updated_at();
