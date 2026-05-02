-- SPDX-License-Identifier: AGPL-3.0-only
-- Copyright (C) 2026 Lumogis
-- Phase 5 Capture: captures + capture_attachments + capture_transcripts.
--
-- Status enums and transcript_provenance are frozen per plan §12.1
-- "MVP freeze 2026-04-29". CHECK constraints encode the freeze so any
-- new value requires an explicit migration — not a silent application
-- change.
--
-- Replay-safe: all statements use CREATE TABLE IF NOT EXISTS / CREATE
-- INDEX IF NOT EXISTS so the file can be re-run against an already-
-- migrated database without error (matching the convention in 019).

-- ── captures (parent row) ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS captures (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          TEXT        NOT NULL DEFAULT 'default',
    status           TEXT        NOT NULL DEFAULT 'pending'
                                 CHECK (status IN ('pending', 'failed', 'indexed')),
    capture_type     TEXT        NOT NULL DEFAULT 'text'
                                 CHECK (capture_type IN ('text', 'url', 'photo', 'voice', 'mixed')),
    title            TEXT,
    text             TEXT,
    url              TEXT        CHECK (char_length(url) <= 2048),
    -- Wire field in OpenAPI is "client_id"; DB column is local_client_id for SQL clarity.
    local_client_id  TEXT,
    note_id          UUID        REFERENCES notes(note_id) ON DELETE SET NULL,
    source_channel   TEXT        NOT NULL DEFAULT 'lumogis_web',
    tags             TEXT[],
    last_error       TEXT,
    -- Optional audit timestamps — NULL until the event occurs.
    captured_at      TIMESTAMPTZ,
    synced_at        TIMESTAMPTZ,
    indexed_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Idempotency: same (user_id, local_client_id) → same capture row.
CREATE UNIQUE INDEX IF NOT EXISTS captures_user_client_id_uidx
    ON captures (user_id, local_client_id)
    WHERE local_client_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS captures_user_id_idx
    ON captures (user_id);

CREATE INDEX IF NOT EXISTS captures_updated_at_idx
    ON captures (updated_at DESC);

-- Reuse the update_updated_at_column() trigger function defined in 003.
CREATE TRIGGER set_captures_updated_at
    BEFORE UPDATE ON captures
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ── capture_attachments (image | audio binary metadata) ───────────────

CREATE TABLE IF NOT EXISTS capture_attachments (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    capture_id           UUID        NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
    user_id              TEXT        NOT NULL DEFAULT 'default',
    attachment_type      TEXT        NOT NULL
                                     CHECK (attachment_type IN ('image', 'audio')),
    -- Server-relative path fragment under LUMOGIS_DATA_DIR captures tree.
    -- Full path assembled by services/media_storage.py — never stored raw.
    storage_key          TEXT        NOT NULL,
    original_filename    TEXT,
    mime_type            TEXT        NOT NULL,
    size_bytes           BIGINT      NOT NULL,
    sha256               TEXT,
    processing_status    TEXT        NOT NULL DEFAULT 'stored'
                                     CHECK (processing_status IN ('stored', 'failed')),
    -- Optional client-supplied idempotency key (local_attachment_id from outbox).
    client_attachment_id TEXT,
    -- Optional: dimensions (image), duration (audio), etc.
    metadata             JSONB,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Idempotency for attachment uploads: replay with same client key → same row.
CREATE UNIQUE INDEX IF NOT EXISTS capture_attachments_client_id_uidx
    ON capture_attachments (user_id, capture_id, client_attachment_id)
    WHERE client_attachment_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS capture_attachments_capture_id_idx
    ON capture_attachments (capture_id);

-- ── capture_transcripts (STT output for audio attachments) ────────────

CREATE TABLE IF NOT EXISTS capture_transcripts (
    id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    capture_id            UUID        NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
    -- App layer enforces attachment_type = audio; the FK alone cannot.
    attachment_id         UUID        NOT NULL REFERENCES capture_attachments(id) ON DELETE CASCADE,
    user_id               TEXT        NOT NULL DEFAULT 'default',
    -- Echo from the STT foundation response — Capture does not own these.
    provider              TEXT,
    model                 TEXT,
    transcript_text       TEXT,
    -- Frozen MVP status set — pin CHECK so new values need a migration.
    transcript_status     TEXT        NOT NULL DEFAULT 'pending'
                                      CHECK (transcript_status IN (
                                          'pending', 'processing', 'complete',
                                          'failed', 'unavailable'
                                      )),
    -- Frozen provenance set (plan §12.1, FP-TBD-5.17 for mobile variants).
    transcript_provenance TEXT        NOT NULL DEFAULT 'server_stt'
                                      CHECK (transcript_provenance IN (
                                          'server_stt',
                                          'mobile_local_stt',
                                          'mobile_direct_provider_stt'
                                      )),
    language              TEXT,
    confidence            REAL,
    error                 TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS capture_transcripts_capture_id_idx
    ON capture_transcripts (capture_id);

CREATE INDEX IF NOT EXISTS capture_transcripts_attachment_id_idx
    ON capture_transcripts (attachment_id);

CREATE TRIGGER set_capture_transcripts_updated_at
    BEFORE UPDATE ON capture_transcripts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
