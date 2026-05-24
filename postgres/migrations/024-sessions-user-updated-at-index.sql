-- Migration 024: index sessions by user recency for memory.get_recent / incremental sync consumers.
-- Idempotent: CREATE INDEX IF NOT EXISTS matches repo convention.
CREATE INDEX IF NOT EXISTS idx_sessions_user_updated_at
    ON sessions (user_id, updated_at DESC);
