-- SPDX-License-Identifier: AGPL-3.0-only
-- Copyright (C) 2026 Lumogis
--
-- LUM-817 — per-mailbox outgoing signature.
--
-- A column on `sources`, following the precedent set by migration 060
-- (`organize_*`) and 067 (`attachment_ingest_*`): per-mailbox email settings
-- live on the mailbox row rather than in a side table. There is one value per
-- mailbox and it is read on the same query that already loads the source, so a
-- separate table would buy nothing but a join.
--
-- PER MAILBOX, not per user, and that is the whole point of the ticket: a
-- household member with a personal mailbox and a shared one needs different
-- signatures, and a per-user signature would sign shared mail with private
-- details.
--
-- DEFAULT '' rather than NULL: every read site wants "the text to append", and
-- an empty string means "nothing to append" without a NULL check at each one.
--
-- NO explicit BEGIN/COMMIT: db_migrations.py runs each file inside one
-- transaction and commits it together with the schema_migrations row.
--
-- Rollback (manual; the runner is apply-only / filename-tracked):
--   ALTER TABLE sources DROP COLUMN signature;

ALTER TABLE sources
    ADD COLUMN IF NOT EXISTS signature TEXT NOT NULL DEFAULT '';

COMMENT ON COLUMN sources.signature IS
    'Outgoing plain-text signature for this mailbox (LUM-817). Appended in the '
    'composer so the user sees and can edit it before sending; NOT appended '
    'server-side, which would double it. Never applied to email_unsubscribe.';
