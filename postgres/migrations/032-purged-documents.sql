-- Migration 032: tombstone for purged personal documents (LUM-500).
-- Stores retry context (file_path, chunk_count) so Qdrant/graph arms can be
-- retried on a subsequent DELETE when the file_index row is already gone.
-- Mirrors purged_conversations (029) with additional state columns for the
-- partial-failure reconciliation path.

BEGIN;

CREATE TABLE IF NOT EXISTS purged_documents (
    user_id         TEXT        NOT NULL,
    document_id     INT         NOT NULL,
    file_path       TEXT        NOT NULL,
    chunk_count     INT         NOT NULL DEFAULT 0,
    qdrant_deleted  BOOLEAN     NOT NULL DEFAULT FALSE,
    graph_deleted   BOOLEAN     NOT NULL DEFAULT FALSE,
    errors          JSONB       NOT NULL DEFAULT '[]',
    purged_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    PRIMARY KEY (user_id, document_id)
);

CREATE INDEX IF NOT EXISTS idx_purged_documents_purged_at
    ON purged_documents (purged_at DESC);

-- Partial index for operator queries and future sweep: only unresolved rows.
CREATE INDEX IF NOT EXISTS idx_purged_documents_unresolved
    ON purged_documents (user_id, document_id)
    WHERE resolved_at IS NULL;

COMMIT;
