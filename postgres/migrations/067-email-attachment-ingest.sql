-- SPDX-License-Identifier: AGPL-3.0-only
-- Copyright (C) 2026 Lumogis
--
-- LUM-702 — email attachments → document library auto-ingest.
--
-- Opt-in, per-mailbox. Defaults OFF so every existing mailbox behaves exactly
-- as before: no document is ever created from someone's mail without an
-- explicit consent step. Additive only (one ALTER with IF NOT EXISTS columns
-- plus one CREATE TABLE IF NOT EXISTS), so this is idempotent on a live volume.
--
-- Rollback (manual DDL; the runner is apply-only / filename-tracked):
--   DROP TABLE IF EXISTS email_attachment_ingest_log;
--   ALTER TABLE sources
--       DROP COLUMN attachment_ingest_enabled,
--       DROP COLUMN attachment_ingest_since;

ALTER TABLE sources
    ADD COLUMN IF NOT EXISTS attachment_ingest_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    -- Watermark: NOW() when the opt-in is first enabled (go-forward only);
    -- "apply to existing" moves it to epoch to widen the sweep over history.
    -- NULL = never opted in. Same semantics as organize_since (LUM-645).
    ADD COLUMN IF NOT EXISTS attachment_ingest_since   TIMESTAMPTZ;

COMMENT ON COLUMN sources.attachment_ingest_enabled IS
    'LUM-702: opt-in to auto-filing this mailbox''s attachments into the '
    'document library. FALSE by default — never enabled implicitly.';
COMMENT ON COLUMN sources.attachment_ingest_since IS
    'LUM-702: go-forward watermark. NOW() on first enable; epoch when the user '
    'asks to apply to existing mail. NULL means never opted in.';

-- ---------------------------------------------------------------------------
-- Provenance + reversal ledger.
--
-- Distinct from external_documents on purpose: that table answers "is this
-- library row stale?", this one answers "which email did this come from, and
-- how do I take it back?".
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS email_attachment_ingest_log (
    id             BIGSERIAL PRIMARY KEY,
    user_id        TEXT NOT NULL DEFAULT 'default',
    source_id      UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    -- TEXT, not UUID. `email_attachment.signal_id` (066) is TEXT, and the
    -- sweep's selection query LEFT JOINs the two: a UUID/TEXT join does not
    -- execute in Postgres without an explicit cast. Declaring it UUID here
    -- would have broken the sweep's central query on its first real run.
    signal_id      TEXT NOT NULL,          -- no FK: email_attachment has none either (066)
    part_index     INTEGER NOT NULL,
    filename       TEXT NOT NULL,          -- sanitised display name
    mime_type      TEXT NOT NULL DEFAULT '',
    size_bytes     BIGINT NOT NULL DEFAULT 0,
    content_sha256 TEXT NOT NULL,          -- sha256 of the ATTACHMENT BYTES
    logical_path   TEXT NOT NULL,          -- email-attachment://{source}/{signal}/{part}
    chunk_count    INTEGER NOT NULL DEFAULT 0,
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reversed_at    TIMESTAMPTZ,
    -- Skip bookkeeping: a row with skipped_reason is a DECISION, not a filing.
    -- It exists so the sweep does not retry an unextractable file every 15
    -- minutes forever, and so the UI can explain the absence.
    skipped_reason TEXT                    -- NULL | no_extractor | blocked_ext | too_large | no_text | duplicate | message_gone | ambiguous
);

-- Idempotency + the dedupe gate in one index: at most one ACTIVE filing per
-- (user, source, content hash). A second copy of the same PDF is refused here,
-- not by an application-level SELECT that races itself.
--
-- PARTIAL on (reversed_at IS NULL AND skipped_reason IS NULL) because a
-- duplicate must still get a row (skipped_reason='duplicate') so the sweep
-- stops reconsidering it, and that row must not itself occupy the active
-- content slot. Reversing a filing frees the slot, so a LATER message carrying
-- the same bytes can be filed.
CREATE UNIQUE INDEX IF NOT EXISTS uq_email_attachment_ingest_active
    ON email_attachment_ingest_log (user_id, source_id, content_sha256)
    WHERE reversed_at IS NULL AND skipped_reason IS NULL;

-- The sweep's "have I already reached a verdict on this part?" probe.
--
-- FULL, not partial — one verdict per part, EVER, and that is deliberate.
-- If this were partial on `reversed_at IS NULL`, the probe would stop seeing a
-- reversed row and the next tick (fifteen minutes later) would re-file the
-- attachment the user had just removed. An undo that silently reverts itself is
-- worse than no undo. The log row IS the memory that a decision was made, and
-- `reversed_at` records which way it went. Consequence, stated rather than
-- hidden: an undone attachment is never re-filed automatically; re-filing needs
-- an explicit user action (follow-up).
CREATE UNIQUE INDEX IF NOT EXISTS uq_email_attachment_ingest_part
    ON email_attachment_ingest_log (user_id, signal_id, part_index);

CREATE INDEX IF NOT EXISTS idx_email_attachment_ingest_source
    ON email_attachment_ingest_log (source_id, ingested_at DESC);

COMMENT ON TABLE email_attachment_ingest_log IS
    'LUM-702: provenance + reversal ledger for attachments auto-filed into the '
    'document library. One row per (user, signal, part) verdict — a filing or a '
    'skip — so the sweep never reconsiders a part it has already judged.';
