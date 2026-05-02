-- SPDX-License-Identifier: AGPL-3.0-only
-- Migration 019 — cross_device_lumogis_web (Phase 0)
--
-- Adds the durable schema the v1 web façade needs:
--   * webpush_subscriptions      — Phase 4 push delivery (active in Phase 0
--                                  CRUD; sender ships in Phase 4)
--   * auth_refresh_revocations   — Phase 1 multi-device tokens scaffold
--                                  (forward-compat; no v1 writer; CI grep
--                                  in `Security decisions` enforces this)
--
-- Both use CREATE TABLE IF NOT EXISTS so the migration is replay-safe and
-- the `db_migrations` runner can reapply it without a reset.

CREATE TABLE IF NOT EXISTS webpush_subscriptions (
    id                       BIGSERIAL PRIMARY KEY,
    user_id                  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint                 TEXT NOT NULL,
    p256dh                   TEXT NOT NULL,
    auth                     TEXT NOT NULL,
    user_agent               TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error               TEXT,
    notify_on_signals        BOOLEAN NOT NULL DEFAULT FALSE,
    notify_on_shared_scope   BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (user_id, endpoint)
);
CREATE INDEX IF NOT EXISTS ix_webpush_user ON webpush_subscriptions (user_id);

-- B11 multi-device refresh tokens — table is INERT in v1.
-- The shipped /api/v1/auth/refresh path uses the single-jti column on
-- `users` (`refresh_token_jti`) and never INSERTs here. Lifted only
-- when the multi-device chunk lands.
CREATE TABLE IF NOT EXISTS auth_refresh_revocations (
    jti        TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    revoked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_refresh_rev_exp
    ON auth_refresh_revocations (expires_at);
