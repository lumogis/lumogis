-- Migration 029: tombstone for purged personal conversations (LUM-162).
-- Prevents in-flight session_end jobs from resurrecting deleted rows and
-- allows DELETE retry when Postgres was removed but Qdrant/graph failed.

BEGIN;

CREATE TABLE IF NOT EXISTS purged_conversations (
    user_id           TEXT NOT NULL,
    conversation_id   UUID NOT NULL,
    purged_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_purged_conversations_purged_at
    ON purged_conversations (purged_at DESC);

COMMIT;
