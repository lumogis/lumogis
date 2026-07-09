-- LUM-585 — admin-managed display name used for "Shared by {member}" attribution.
-- Nullable: empty by default, and cleared back to NULL when an admin blanks it,
-- so the read-time label falls back to the email local-part.

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS display_name TEXT NULL;

COMMENT ON COLUMN users.display_name IS
  'Admin-managed display label (LUM-585). When non-empty it is shown as the '
  '"Shared by {member}" attribution on shared items; NULL falls back to the '
  'email local-part.';
