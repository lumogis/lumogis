-- Migration 003: sessions, notes, audio_memos tables + graph projection tracking
-- NOTE: EXECUTE FUNCTION syntax requires Postgres 11+; verified against pinned Postgres 16.8.

-- Session canonical record (Qdrant holds semantic embeddings; Postgres is source of truth)
CREATE TABLE IF NOT EXISTS sessions (
    session_id          UUID PRIMARY KEY,
    summary             TEXT NOT NULL DEFAULT '',
    topics              TEXT[] NOT NULL DEFAULT '{}',
    entities            TEXT[] NOT NULL DEFAULT '{}',
    user_id             TEXT NOT NULL DEFAULT 'default',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    graph_projected_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS notes (
    note_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    text                TEXT NOT NULL,
    user_id             TEXT NOT NULL DEFAULT 'default',
    source              TEXT NOT NULL DEFAULT 'quick_capture',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    graph_projected_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS audio_memos (
    audio_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_path           TEXT NOT NULL,
    transcript          TEXT,
    duration_seconds    FLOAT,
    whisper_model       TEXT,
    user_id             TEXT NOT NULL DEFAULT 'default',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    transcribed_at      TIMESTAMPTZ,
    graph_projected_at  TIMESTAMPTZ
);

-- Persist resolved entity UUIDs with each session so reconciliation can replay
-- DISCUSSED_IN edges via UUID lookup rather than name-string fallback.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS entity_ids TEXT[] NOT NULL DEFAULT '{}';

-- Add graph projection tracking to existing tables
ALTER TABLE entities ADD COLUMN IF NOT EXISTS graph_projected_at TIMESTAMPTZ;
ALTER TABLE file_index ADD COLUMN IF NOT EXISTS graph_projected_at TIMESTAMPTZ;

-- Auto-update updated_at on row modification (required for reconciliation drift detection)
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

-- CREATE OR REPLACE TRIGGER requires Postgres 14+. Pinned image is 16.8.
-- Using OR REPLACE so the migration runner can re-apply this file safely.
CREATE OR REPLACE TRIGGER set_updated_at BEFORE UPDATE ON sessions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER set_updated_at BEFORE UPDATE ON notes
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER set_updated_at BEFORE UPDATE ON audio_memos
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Indexes for CONTEXT_BUILDING entity lookup (case-insensitive name + alias search)
-- Required for the CONTEXT_BUILDING hook's sub-10ms entity lookup claim.
CREATE INDEX IF NOT EXISTS idx_entities_name_lower ON entities (LOWER(name));
CREATE INDEX IF NOT EXISTS idx_entities_aliases_gin ON entities USING GIN (aliases);
