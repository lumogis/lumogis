-- Migration 023: auth_sessions + users.token_version (LUM-29).
--
-- Browser/device sessions live in ``auth_sessions`` — NOT ``sessions``
-- from migration 003 (chat/memory). Naming collision would make
-- ``CREATE TABLE IF NOT EXISTS sessions`` a silent no-op.
--
-- Sequencing: requires 010-users-and-roles. Idempotent / re-runnable.

BEGIN;

CREATE TABLE IF NOT EXISTS auth_sessions (
    id                  TEXT        PRIMARY KEY,                    -- uuid4 hex; equals refresh JWT ``jti``
    user_id             TEXT        NOT NULL,
    family_id           TEXT        NOT NULL,                       -- reuse-detection cohort
    refresh_token_hash  TEXT        NOT NULL
        CHECK (length(refresh_token_hash) = 64),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at        TIMESTAMPTZ NULL,
    expires_at          TIMESTAMPTZ NOT NULL,
    revoked_at          TIMESTAMPTZ NULL,
    device_label        TEXT        NOT NULL
        CHECK (length(device_label) BETWEEN 1 AND 64),
    ip_hash             TEXT        NOT NULL
        CHECK (length(ip_hash) = 64),
    ua_hash             TEXT        NOT NULL
        CHECK (length(ua_hash) = 64)
);

CREATE INDEX IF NOT EXISTS auth_sessions_user_created_idx
    ON auth_sessions (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS auth_sessions_family_idx
    ON auth_sessions (family_id);

CREATE INDEX IF NOT EXISTS auth_sessions_user_idx
    ON auth_sessions (user_id);

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS token_version BIGINT NOT NULL DEFAULT 1;

COMMIT;
