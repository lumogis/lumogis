-- 085-instance-setup.sql (LUM-981): the setup-complete marker.
-- One row, ever. Written only by POST /api/v1/setup/claim in the same
-- transaction that creates the first admin. Deleting the admin later
-- does not reopen setup (ON DELETE SET NULL keeps the row).
--
-- Rollback: DROP TABLE IF EXISTS instance_setup;  (no other table references it)
CREATE TABLE IF NOT EXISTS instance_setup (
    id            INTEGER     PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    admin_user_id TEXT        NULL REFERENCES users(id) ON DELETE SET NULL,
    completed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
