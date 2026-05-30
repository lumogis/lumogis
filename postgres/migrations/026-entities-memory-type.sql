-- LUM-124: optional memory classification + verification timestamp on entities.
ALTER TABLE entities ADD COLUMN IF NOT EXISTS memory_type TEXT;
ALTER TABLE entities DROP CONSTRAINT IF EXISTS entities_memory_type_check;
ALTER TABLE entities ADD CONSTRAINT entities_memory_type_check
    CHECK (memory_type IS NULL OR memory_type IN ('user_preference', 'correction', 'relationship'));

ALTER TABLE entities ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_entities_user_memory_correction
    ON entities (user_id, memory_type)
    WHERE memory_type = 'correction';
