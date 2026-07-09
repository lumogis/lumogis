BEGIN;

CREATE TABLE IF NOT EXISTS user_invites (
    id              TEXT        PRIMARY KEY,
    token_prefix    TEXT        NOT NULL,
    token_hash      TEXT        NOT NULL,
    role            TEXT        NOT NULL CHECK (role IN ('admin', 'user')),
    allows_shared   BOOLEAN     NOT NULL DEFAULT TRUE,
    created_by      TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL,
    used_at         TIMESTAMPTZ NULL,
    used_by         TEXT        NULL,
    revoked_at      TIMESTAMPTZ NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS user_invites_active_prefix_uniq
    ON user_invites (token_prefix) WHERE used_at IS NULL AND revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS user_invites_created_by_idx ON user_invites (created_by);
CREATE INDEX IF NOT EXISTS user_invites_expires_at_idx ON user_invites (expires_at);

COMMIT;
