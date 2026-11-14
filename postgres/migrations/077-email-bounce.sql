-- SPDX-License-Identifier: AGPL-3.0-only
-- Copyright (C) 2026 Lumogis
--
-- LUM-820 — record a delivery-status notification against the send it reports on.
--
-- SMTP submission succeeding means the provider ACCEPTED the message, not that
-- it was delivered. Without this the audit row says the send succeeded and the
-- bounce sits in a triage lane the user may never open, so the product reports
-- a delivery it cannot vouch for.
--
-- One row per (send, recipient): a single message to three addresses can bounce
-- for one of them, and collapsing that would report the whole send as failed.
--
-- `sent_log_id` is NOT a foreign key on purpose. A bounce can arrive after the
-- send row has been pruned, and a dangling row that still names the recipient
-- and status is more useful than an insert that fails at 3am. Correlation
-- already happened in Python; this table is the record of it.
--
-- NO explicit BEGIN/COMMIT: db_migrations.py wraps each file in one transaction.
--
-- Rollback (manual; the runner is apply-only / filename-tracked):
--   DROP TABLE IF EXISTS email_bounce;

CREATE TABLE IF NOT EXISTS email_bounce (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      TEXT        NOT NULL,
    sent_log_id  UUID,
    message_id   TEXT        NOT NULL DEFAULT '',
    recipient    TEXT        NOT NULL DEFAULT '',
    status       TEXT        NOT NULL DEFAULT '',
    -- 5.x.x. A 4.x.x is a retry still in progress and must not be surfaced as a
    -- failure, so the distinction is stored rather than re-derived per read.
    permanent    BOOLEAN     NOT NULL DEFAULT TRUE,
    seen_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Set when the user has been told. Without it the surface is a banner that
    -- can never be dismissed, which is how a warning becomes wallpaper: the
    -- next real bounce arrives into a notice the user already stopped reading.
    acknowledged_at TIMESTAMPTZ
);

-- The read is "what has this user not been told about yet".
CREATE INDEX IF NOT EXISTS email_bounce_user_idx
    ON email_bounce (user_id, seen_at DESC);
CREATE INDEX IF NOT EXISTS email_bounce_unacked_idx
    ON email_bounce (user_id, seen_at DESC)
    WHERE acknowledged_at IS NULL AND permanent;

-- A provider may deliver the same report more than once; the same bounce must
-- not surface twice for the same send and recipient.
CREATE UNIQUE INDEX IF NOT EXISTS email_bounce_dedupe_idx
    ON email_bounce (user_id, message_id, recipient);
