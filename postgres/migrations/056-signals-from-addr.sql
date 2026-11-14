-- LUM-642: persist the email sender on signals so the /email surface can render
-- it (bare address + display name). Additive, idempotent, no backfill — historical
-- rows keep ''. Email signals ingested after this carry the real values.
ALTER TABLE signals ADD COLUMN IF NOT EXISTS from_addr    TEXT NOT NULL DEFAULT '';
ALTER TABLE signals ADD COLUMN IF NOT EXISTS from_display TEXT NOT NULL DEFAULT '';
