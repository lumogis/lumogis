-- LUM-577 — per-user shared-scope capability (derived from invite redemption).
-- Default TRUE preserves existing household members and admin-created accounts.

ALTER TABLE users ADD COLUMN IF NOT EXISTS allows_shared BOOLEAN NOT NULL DEFAULT TRUE;
