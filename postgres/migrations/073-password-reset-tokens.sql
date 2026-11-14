-- Migration 073: forgot-password reset tokens + system mail outbox (LUM-65).
-- SPDX-License-Identifier: AGPL-3.0-only
--
-- WHY (tokens): a forgotten password was a hard stop — a member had to ask an
-- admin and a sole admin needed shell access. `password_reset_tokens` is the
-- invite-shaped public token table that backs the email magic-link reset:
-- plaintext `prst_` + base32 body is never stored, only its SHA-256, and the
-- first 16 characters of the body are the (unique) lookup prefix.
--
-- WHY (outbox): the reset mail is NOT sent on the request path. The token row
-- and its outbox row are inserted in ONE transaction, so a committed request
-- means "this mail is durably owed" and mint-without-mail is impossible. Two
-- properties fall out of that which a synchronous send cannot have:
--
--   1. Enumeration safety. `/password-reset/request` answers identically for a
--      known and an unknown address, and the timing pad that makes those two
--      paths indistinguishable only works because neither branch does network
--      I/O — a wall-clock floor cannot mask a branch that runs to a 30s SMTP
--      timeout, and the gap widens exactly when the operator's relay is sick.
--   2. Availability. Orchestrator routes are sync `def`, so they run in a
--      shared, bounded AnyIO threadpool. A 30s SMTP hold on an unauthenticated
--      public route would let a slow relay — or a handful of source IPs — starve
--      POST /api/v1/auth/login. The anti-lockout feature would become the lockout.
--
-- SECRET-BEARING: `system_mail_outbox.body` contains the plaintext reset URL.
-- The drain DELETEs rows on success rather than stamping them, `last_error`
-- never echoes the body, and unsent rows are reaped at the token TTL.
--
-- Manual downgrade: DROP TABLE password_reset_tokens; DROP TABLE system_mail_outbox;
--
-- Idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id UUID PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_prefix TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    UNIQUE (token_prefix)
);

CREATE INDEX IF NOT EXISTS password_reset_tokens_user_id_idx
    ON password_reset_tokens (user_id);

CREATE INDEX IF NOT EXISTS password_reset_tokens_expires_at_idx
    ON password_reset_tokens (expires_at);

COMMENT ON TABLE password_reset_tokens IS
    'LUM-65: hashed, single-use forgot-password tokens. Mirrors user_invites '
    '(prefix lookup + SHA-256 verify + atomic consume) with prefix tag prst_. '
    'Minting for a user marks that user''s prior unused tokens used, so only '
    'the newest link works; services.users._apply_new_password does the same on '
    'ANY password change, so an admin or CLI reset also kills a live emailed '
    'link. Redeem consumes the row and calls _apply_new_password, which bumps '
    'token_version and revokes every auth session.';

CREATE TABLE IF NOT EXISTS system_mail_outbox (
    id UUID PRIMARY KEY,
    to_addr TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    attempts INT NOT NULL DEFAULT 0,
    sent_at TIMESTAMPTZ,
    last_error TEXT
);

-- Partial: the drain only ever asks for unsent rows that are due.
CREATE INDEX IF NOT EXISTS system_mail_outbox_due_idx
    ON system_mail_outbox (next_attempt_at)
    WHERE sent_at IS NULL;

COMMENT ON TABLE system_mail_outbox IS
    'LUM-65: durable queue for transactional system mail, drained by an '
    'APScheduler job (see services.system_mail.drain_outbox). Enqueued inside '
    'the caller''s transaction so a committed password-reset request cannot '
    'exist without the mail it owes. SECRET-BEARING: body carries the plaintext '
    'reset URL, so successful sends DELETE the row rather than stamping sent_at, '
    'and undeliverable rows are dead-lettered at the retry budget. sent_at is '
    'retained for callers that enqueue non-secret mail.';

COMMIT;
