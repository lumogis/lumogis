-- Migration 036: users.last_seen_at (LUM-334 — household RBAC, last-active tracking)
-- Nullable: NULL = never seen / pre-migration. Written by services.users.touch_last_seen
-- (throttled conditional UPDATE on the authenticated request path) and surfaced in
-- GET /api/v1/admin/users via UserAdminView. Idempotent (IF NOT EXISTS).

ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ NULL;
