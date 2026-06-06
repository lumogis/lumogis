-- LUM-216: per-user first wow-moment path dismissal timestamp (nullable = cards may show).
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS wow_dismissed_at TIMESTAMPTZ NULL;

COMMENT ON COLUMN users.wow_dismissed_at IS
  'First wow-moment path dismissed (LUM-216); NULL = cards may show when entities_ready.';
