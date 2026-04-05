-- Migration 002: app_settings table for dashboard settings overrides
-- Stores filesystem_root (pending), API key env vars, default_model.
-- Fresh installs get this from init.sql; run this for existing DBs.
--
-- Apply with (from project root):
--   docker compose exec postgres psql -U lumogis -d lumogis -c "CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW());"
-- Or pipe the file:
--   cat postgres/migrations/002-app-settings.sql | docker compose exec -T postgres psql -U lumogis -d lumogis -f -

CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
