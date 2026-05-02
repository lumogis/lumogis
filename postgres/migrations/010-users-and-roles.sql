-- Migration 010: Users and roles for family-LAN multi-user.
--
-- Introduces the `users` table that owns email/password/role for human
-- accounts. Bi-state behaviour:
--   AUTH_ENABLED=false  -> single-user dev; the table can stay empty (a
--                         synthesised UserContext("default", role="admin")
--                         is used by the orchestrator).
--   AUTH_ENABLED=true   -> family-LAN; login required, no anonymous default.
--
-- `refresh_token_jti` holds the currently-active refresh-JWT jti for the
-- user. NULL means "no active refresh session". Single-active-jti per user
-- is the v1 contract (a second login on a different device evicts the first).
-- Disabling or deleting a user clears the column for instant refresh-side
-- revocation; access tokens still survive their TTL (<=15 min).
--
-- No foreign keys point to users in v1 — other tables keep their existing
-- free-form `user_id TEXT` columns. `user_id` values become uuid4 hex
-- strings produced by this table for non-`default` users; `'default'`
-- remains valid only in AUTH_ENABLED=false dev mode.

CREATE TABLE IF NOT EXISTS users (
    id                  TEXT PRIMARY KEY,                  -- uuid4 hex
    email               TEXT NOT NULL UNIQUE,
    password_hash       TEXT NOT NULL,                     -- argon2id encoded string
    role                TEXT NOT NULL CHECK (role IN ('admin', 'user')),
    disabled            BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at       TIMESTAMPTZ,
    refresh_token_jti   TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_email_active
    ON users (lower(email)) WHERE disabled = FALSE;
