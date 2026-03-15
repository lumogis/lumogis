-- Lumogis Postgres schema
-- Runs once on first container start (docker-entrypoint-initdb.d/).
-- Add new tables here; never alter existing columns without a migration.

-- Tracks every ingested document: path, content hash, chunk count, OCR flag.
-- file_hash enables re-ingest skip: if hash unchanged, skip chunking + embedding.
CREATE TABLE IF NOT EXISTS file_index (
    id          SERIAL PRIMARY KEY,
    file_path   TEXT UNIQUE NOT NULL,
    file_hash   TEXT NOT NULL,
    file_type   TEXT NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    ocr_used    BOOLEAN NOT NULL DEFAULT FALSE,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Extracted entities: people, organisations, projects, concepts.
-- context_tags drive entity resolution (Chunk 9): overlap >= 2 tags -> merge.
-- aliases accumulates alternative names seen across sessions/documents.
CREATE TABLE IF NOT EXISTS entities (
    entity_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT NOT NULL,
    entity_type  TEXT NOT NULL,        -- PERSON | ORG | PROJECT | CONCEPT | FILE
    aliases      TEXT[] NOT NULL DEFAULT '{}',
    context_tags TEXT[] NOT NULL DEFAULT '{}',
    mention_count INTEGER NOT NULL DEFAULT 1,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Provenance edges: where was each entity seen?
-- relation_type: MENTIONED_IN_SESSION | MENTIONED_IN_DOCUMENT | RELATED_TO
-- evidence_type: SESSION | DOCUMENT
-- evidence_id:   session UUID or file_path
CREATE TABLE IF NOT EXISTS entity_relations (
    id            SERIAL PRIMARY KEY,
    source_id     UUID NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    evidence_id   TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Ambiguous entity merge candidates flagged for manual review (Chunk 9).
-- Resolved via psql directly until a review UI is built (Phase 4+).
CREATE TABLE IF NOT EXISTS review_queue (
    id              SERIAL PRIMARY KEY,
    candidate_a_id  UUID REFERENCES entities(entity_id) ON DELETE CASCADE,
    candidate_b_id  UUID REFERENCES entities(entity_id) ON DELETE CASCADE,
    reason          TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Ask/Do permission model: per-connector, per-action-type enforcement.
-- Every MCP connector starts in ASK mode. DO mode is explicitly enabled
-- per connector. routine_do tracks approval counts for Ask/Do++ elevation.
CREATE TABLE IF NOT EXISTS connector_permissions (
    id              SERIAL PRIMARY KEY,
    connector       TEXT NOT NULL,         -- e.g. 'filesystem-mcp', 'email-mcp'
    mode            TEXT NOT NULL DEFAULT 'ASK',  -- ASK | DO
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(connector)
);

-- Ask/Do++ routine automation: tracks per-action-type approval history.
-- When approval_count reaches threshold (default 15) without edits,
-- Lumogis can prompt the user to auto-approve that action type.
-- Phase 2: table created. Phase 4: routine Do automation uses it.
CREATE TABLE IF NOT EXISTS routine_do_tracking (
    id              SERIAL PRIMARY KEY,
    connector       TEXT NOT NULL,
    action_type     TEXT NOT NULL,         -- e.g. 'reply_email', 'tag_photo'
    approval_count  INTEGER NOT NULL DEFAULT 0,
    edit_count      INTEGER NOT NULL DEFAULT 0,
    auto_approved   BOOLEAN NOT NULL DEFAULT FALSE,
    granted_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(connector, action_type)
);

-- Action audit log: every read (ASK) and write (DO) action is logged.
-- Reversible actions store the reverse_action for undo capability.
CREATE TABLE IF NOT EXISTS action_log (
    id              SERIAL PRIMARY KEY,
    connector       TEXT NOT NULL,
    action_type     TEXT NOT NULL,
    mode            TEXT NOT NULL,         -- ASK | DO | ROUTINE_DO
    allowed         BOOLEAN NOT NULL DEFAULT TRUE,
    input_summary   TEXT,
    result_summary  TEXT,
    reverse_action  JSONB,                 -- null if irreversible
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Tracks how positions on topics evolve over time (Phase 3 activation).
-- Populated by richer entity extraction when it can reliably detect
-- position changes across sessions. Schema created now to avoid migration.
-- CREATE TABLE IF NOT EXISTS position_changes (
--     id              SERIAL PRIMARY KEY,
--     entity_id       UUID REFERENCES entities(entity_id) ON DELETE CASCADE,
--     topic           TEXT NOT NULL,
--     old_position    TEXT,
--     new_position    TEXT NOT NULL,
--     evidence_id     TEXT NOT NULL,       -- session UUID or file_path
--     evidence_type   TEXT NOT NULL,       -- SESSION | DOCUMENT
--     detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
-- );
