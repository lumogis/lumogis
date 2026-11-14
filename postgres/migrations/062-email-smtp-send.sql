-- Migration 062: SMTP send — sent-log + per-proposal risk annotation (LUM-650).
-- SPDX-License-Identifier: AGPL-3.0-only
--
-- Two additive changes for the first outbound email capability (reply +
-- compose-new, sent as the user through their own provider's SMTP submission):
--
--   (a) email_sent_log — an audit of what Lumogis sent, and the lookup that
--       powers the first-contact check (has this user ever emailed this
--       address before?). User-scoped; the GIN index on to_addrs makes the
--       "any recipient never contacted" membership test cheap.
--
--   (b) action_proposals.annotation — a per-proposal risk-annotation store
--       (LUM-623 create-time `ActionSpec.annotate` hook). `payload` (migration
--       022) is the VALIDATED action input and must not be overloaded; the
--       annotation is a separate approval-relevant fact computed at proposal
--       create and read by the Ask/Do resolver (`annotation->>'first_contact'`).
--       General mechanism — not email-only; future risk-annotated actions reuse
--       this column rather than adding parallel ones.
--
-- The `connector` column of user_connector_credentials is free TEXT constrained
-- only by `^[a-z0-9_]+$` (migration 015), which already admits 'smtp' — so the
-- SMTP credential needs NO schema change here.
--
-- Manual downgrade (operator-only, no automated rollback in-repo):
--   DROP TABLE email_sent_log;
--   ALTER TABLE action_proposals DROP COLUMN annotation;
--
-- Idempotent.

BEGIN;

-- (a) Outbound-send audit + first-contact lookup.
CREATE TABLE IF NOT EXISTS email_sent_log (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      TEXT NOT NULL DEFAULT 'default',
    source_id    TEXT NOT NULL,                    -- the mailbox sent from
    to_addrs     TEXT[] NOT NULL DEFAULT '{}',     -- normalized addr-specs
    subject      TEXT NOT NULL DEFAULT '',
    message_id   TEXT NOT NULL DEFAULT '',
    in_reply_to  TEXT,
    action       TEXT NOT NULL DEFAULT 'compose',  -- 'reply' | 'compose'
    sent_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_email_sent_log_user
    ON email_sent_log (user_id, sent_at DESC);
-- First-contact membership test: "is this recipient in anything I've sent?"
CREATE INDEX IF NOT EXISTS idx_email_sent_log_to
    ON email_sent_log USING GIN (to_addrs);

-- (b) Per-proposal risk annotation (create-time; read by the Ask/Do resolver).
ALTER TABLE action_proposals
    ADD COLUMN IF NOT EXISTS annotation JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN action_proposals.annotation IS
    'LUM-650: create-time risk annotation (ActionSpec.annotate). Read by the '
    'Ask/Do resolver — e.g. annotation->>''first_contact'' forces Ask. Distinct '
    'from payload (the validated action input). General; not email-only.';

COMMIT;
