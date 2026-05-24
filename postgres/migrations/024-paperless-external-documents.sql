-- LUM-281 — paperless-ngx ingest: poll cursor + external document bookkeeping.
-- Rollback (operator): DROP TABLE external_documents;
--                       ALTER TABLE sources DROP COLUMN IF EXISTS poll_cursor;
-- Qdrant orphan cleanup may be required after full rollback — see plan.

-- sources: opaque cursor for incremental REST polling (paperless added__gt watermark)
ALTER TABLE sources ADD COLUMN IF NOT EXISTS poll_cursor TEXT;

CREATE TABLE IF NOT EXISTS external_documents (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             TEXT NOT NULL,
    source_id           UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    external_kind       TEXT NOT NULL,
    external_id         TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    chunk_count         INTEGER NOT NULL DEFAULT 0,
    logical_path        TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, source_id, external_kind, external_id)
);

CREATE INDEX IF NOT EXISTS external_documents_user_source_idx
    ON external_documents (user_id, source_id);
