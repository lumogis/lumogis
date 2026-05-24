-- LUM-165: per-user first-run onboarding completion timestamp (nullable = not completed).
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS onboarding_completed_at TIMESTAMPTZ NULL;

COMMENT ON COLUMN users.onboarding_completed_at IS
  'When set, Lumogis Web skips the first-run onboarding modal for this user.';
