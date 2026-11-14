-- SPDX-License-Identifier: AGPL-3.0-only
-- Copyright (C) 2026 Lumogis
--
-- Persist each email signal's RFC 5322 Message-ID.
--
-- Lumogis stores no attachment bytes (LUM-695): opening one refetches the part
-- from IMAP by uid. A uid is only valid within one folder, so any move breaks
-- the refetch — and `email_attachment_ingest.py` then records `message_gone`,
-- which is TERMINAL under the full `uq_..._part` index (067). The attachment is
-- never offered again.
--
-- There is already a Message-ID fallback for exactly this, but it reads from
-- `email_organize_log`, so it only exists when LUMOGIS'S OWN organize moved the
-- message. Every other mover — the user in Apple Mail, a paperless-ngx mail
-- rule, any other client, a server-side filter — leaves no such row and gets the
-- terminal skip. Since paperless's mail rules move or delete on success by
-- default, a user running both loses attachments systematically and silently.
--
-- Storing it on the signal makes the fallback available for ANY move, from any
-- cause, because the header travelled with the message when we first read it.
-- `Message-ID` is already fetched (`_HEADER_FIELDS` in adapters/
-- email_imap_source.py), so this costs no extra IMAP work.
--
-- NOT unique, deliberately. Message-ID is authored by the SENDER and nothing
-- enforces its uniqueness; the adapter already refuses to guess when several
-- messages share one (email_imap_source.py:607-622). A UNIQUE constraint here
-- would turn a malformed or malicious duplicate into a failed ingest of
-- legitimate mail.
--
-- Additive and backfill-free. Existing rows keep '', which is exactly the
-- pre-migration behaviour: no stored Message-ID, fall back to the uid.
--
-- Rollback (manual; the runner is apply-only / filename-tracked):
--   ALTER TABLE signals DROP COLUMN email_message_id;

ALTER TABLE signals
    ADD COLUMN IF NOT EXISTS email_message_id TEXT NOT NULL DEFAULT '';

COMMENT ON COLUMN signals.email_message_id IS
    'RFC 5322 Message-ID for email signals; empty for every other source type. '
    'Lets an attachment refetch survive a message being moved by ANY client or '
    'external system, not only by Lumogis''s own organize (which is all the '
    'email_organize_log fallback could cover). Not unique: the header is '
    'sender-authored and nothing enforces uniqueness.';

-- Lookup is by (user_id, signal_id) from the attachment path, which the primary
-- key already serves. No index here: this column is READ per signal, never
-- searched across signals, and an unused index on a hot insert path is a cost
-- with no reader.
