-- SPDX-License-Identifier: AGPL-3.0-only
-- Copyright (C) 2026 Lumogis
--
-- LUM-697 — unsubscribe targets + per-sender suppression rules.
--
-- Additive only: two CREATE TABLE IF NOT EXISTS, no ALTER of an existing table,
-- so this is idempotent and safe to re-run on an existing volume. No backfill —
-- the live ingest path populates going forward (LUM-711 covers history).
--
-- Rollback: DROP TABLE IF EXISTS email_sender_rule, email_unsubscribe_target;
-- Nothing else references either table; `signals` is untouched.

-- ---------------------------------------------------------------------------
-- One row per SENDER, not per message. 200 newsletters from one retailer
-- collapse to a single row carrying a message count.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS email_unsubscribe_target (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         TEXT        NOT NULL DEFAULT 'default',
    -- UUID + FK, matching email_organize_log (migration 060). A subscription row
    -- is meaningless without its mailbox, so the cascade is correct here — unlike
    -- `signals`, which LUM-701 deliberately keeps after a mailbox delete because
    -- each row carries denormalised source_label/source_url and is readable history.
    source_id       UUID        NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    from_addr       TEXT        NOT NULL,
    from_display    TEXT        NOT NULL DEFAULT '',
    mailto_addr     TEXT        NOT NULL DEFAULT '',
    mailto_subject  TEXT        NOT NULL DEFAULT '',
    https_uri       TEXT        NOT NULL DEFAULT '',
    one_click       BOOLEAN     NOT NULL DEFAULT FALSE,
    message_count   INTEGER     NOT NULL DEFAULT 0,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    unsubscribed_at TIMESTAMPTZ,
    UNIQUE (user_id, source_id, from_addr)
);

COMMENT ON COLUMN email_unsubscribe_target.https_uri IS
    'RFC 8058 one-click URI, stored verbatim and NEVER fetched server-side. '
    'Handed to the user browser only. See cursor/adrs/email-unsubscribe.md — '
    'server-side fetching of message-derived URIs is closed by decision, not '
    'omission. LUM-708 is the deferred issue that may change this, and only '
    'after CONNECT-proxy containment and inbound DKIM verification exist.';

COMMENT ON COLUMN email_unsubscribe_target.one_click IS
    'Sender advertised List-Unsubscribe-Post: List-Unsubscribe=One-Click. '
    'Display + future-use only. It grants NOTHING today: RFC 8058 requires the '
    'header to be DKIM-signed so a receiver can tell it from a forgery, and this '
    'codebase has no inbound DKIM verification (LUM-707). Do not gate any '
    'automated action on this column until that exists.';

COMMENT ON COLUMN email_unsubscribe_target.unsubscribed_at IS
    'Stamped as each target own send succeeds, not at end of batch. The handler '
    'skips already-stamped targets, which is what makes a retry of a partially '
    'completed batch idempotent (LUM-697).';

-- The Subscriptions view has exactly one query shape: this user's senders for a
-- mailbox, busiest first.
CREATE INDEX IF NOT EXISTS idx_unsub_target_user_count
    ON email_unsubscribe_target (user_id, source_id, message_count DESC);

-- ---------------------------------------------------------------------------
-- Standing per-sender suppression. v1 supports exactly one action: 'hide'.
-- The column + CHECK exist so 'mark_read' can be added by LUM-709 without a
-- second migration.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS email_sender_rule (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    TEXT        NOT NULL DEFAULT 'default',
    source_id  UUID        NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    from_addr  TEXT        NOT NULL,
    action     TEXT        NOT NULL,
    active     BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, source_id, from_addr),
    CONSTRAINT email_sender_rule_action_ck CHECK (action IN ('hide'))
);

COMMENT ON TABLE email_sender_rule IS
    'v1 is hide-only (LUM-697): the message is never surfaced, and NO IMAP '
    'operation is performed — nothing is written to the mailbox or to '
    'email_organize_log. mark_read/trash were cut because apply_organize accepts '
    'only label/move/trash, _pending_for_op cannot retry work with no signals '
    'row, and undo_tick reverses only {label, move}. LUM-709 carries them.';

CREATE INDEX IF NOT EXISTS idx_sender_rule_lookup
    ON email_sender_rule (user_id, source_id, from_addr) WHERE active;
