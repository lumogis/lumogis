-- SPDX-License-Identifier: AGPL-3.0-only
-- Copyright (C) 2026 Lumogis
--
-- LUM-695 — email attachments: inbound metadata + outbound staging index.
--
-- Additive only: two CREATE TABLE IF NOT EXISTS plus their indexes, no ALTER of
-- an existing table, so this is idempotent and safe to re-run on an existing
-- volume. No backfill — inbound metadata is written by the live full-ingest
-- path going forward, and staged rows are created by the composer.
--
-- Rollback: DROP TABLE IF EXISTS email_attachment_staged, email_attachment;
-- Nothing else references either table.

-- ---------------------------------------------------------------------------
-- Inbound: one row per attachment PART of a full-ingest email signal.
--
-- No FK on signal_id — `signals.signal_id` is TEXT and the email family does
-- not FK it (LUM-701/697 precedent). Deleting a signal orphans metadata; the
-- owner-scoped read makes that harmless (an orphan is unreachable, because the
-- route resolves ownership through the signal row that no longer exists).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS email_attachment (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      TEXT        NOT NULL DEFAULT 'default',
    signal_id    TEXT        NOT NULL,
    part_index   INTEGER     NOT NULL,
    filename     TEXT        NOT NULL,   -- SANITISED at extract, never raw
    mime_type    TEXT        NOT NULL DEFAULT '',
    size_bytes   BIGINT      NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, signal_id, part_index)
);

CREATE INDEX IF NOT EXISTS idx_email_attachment_signal
    ON email_attachment (user_id, signal_id);

COMMENT ON TABLE email_attachment IS
    'Metadata only — attachment BYTES are never stored. They are fetched on '
    'demand from IMAP (LUM-695 decision). Consequence: opening an attachment '
    'needs the mailbox reachable, and a message deleted upstream loses it.';

COMMENT ON COLUMN email_attachment.part_index IS
    'The enumeration index of services.email_attachments._walk_parts — NOT an '
    'RFC 3501 IMAP part number. The two numberings diverge on nested multipart '
    'and message/rfc822 messages. It is only ever resolved by that same helper, '
    'which is what makes write and read consistent by construction.';

COMMENT ON COLUMN email_attachment.size_bytes IS
    'DECODED size (what get_payload(decode=True) returns), not the base64 wire '
    'size (~33%% larger). The caps are user-facing "how big is this file" '
    'numbers, so comparing an encoded size against them would refuse files that '
    'are actually well under the limit.';

-- ---------------------------------------------------------------------------
-- Outbound: index of files staged on disk by the composer, awaiting a send.
--
-- storage_key is a SERVER-controlled relative path ('{id}/blob'); the client
-- filename lives only in `filename` and never becomes a path component.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS email_attachment_staged (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      TEXT        NOT NULL DEFAULT 'default',
    filename     TEXT        NOT NULL,
    mime_type    TEXT        NOT NULL DEFAULT '',
    size_bytes   BIGINT      NOT NULL DEFAULT 0,
    storage_key  TEXT        NOT NULL,
    -- The link the sweep keys on. NULL until a proposal is created; set at
    -- propose time. Without it the proposal-state sweep cannot be written.
    proposal_id  INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    consumed_at  TIMESTAMPTZ
);

COMMENT ON TABLE email_attachment_staged IS
    'Outbound staging index (LUM-695). Swept by PROPOSAL STATE, not wall clock: '
    'action_proposals rows never expire, so a file whose proposal is still '
    'pending/approved/claimed/executing is NEVER swept however old. Only an '
    'UNREFERENCED row (proposal_id IS NULL — an abandoned composer) ages out on '
    'the TTL, and a terminal proposal (done/rejected/archived/dead) is swept '
    'promptly regardless of age.';

COMMENT ON COLUMN email_attachment_staged.storage_key IS
    'Server-controlled relative path under EMAIL_ATTACHMENT_STAGING_ROOT, of the '
    'form "{id}/blob". The client filename is NEVER a path component — that '
    'eliminates the Unicode-normalisation / case-collision / reserved-name class '
    'outright rather than relying on a sanitiser to catch it.';

-- Two sweep queries: unreferenced-and-old, and referenced-but-terminal.
CREATE INDEX IF NOT EXISTS idx_email_attachment_staged_orphans
    ON email_attachment_staged (created_at) WHERE consumed_at IS NULL AND proposal_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_email_attachment_staged_proposal
    ON email_attachment_staged (proposal_id) WHERE consumed_at IS NULL;
