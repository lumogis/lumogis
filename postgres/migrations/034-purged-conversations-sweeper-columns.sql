-- SPDX-License-Identifier: AGPL-3.0-only
-- Copyright (C) 2026 Lumogis
--
-- Migration 034: add reconciliation-sweeper columns to purged_conversations (LUM-416).
--
-- Mirrors the richer tombstone schema of purged_documents (migration 032) so the
-- APScheduler sweeper can track which store arms have succeeded and bound retries.
-- Existing rows get safe defaults (FALSE / 0 / NULL) — the sweeper will attempt them
-- once and mark resolved_at when both arms succeed.

BEGIN;

ALTER TABLE purged_conversations
    ADD COLUMN IF NOT EXISTS qdrant_deleted  BOOLEAN     NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS graph_deleted   BOOLEAN     NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS errors          JSONB       NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS sweep_attempts  INT         NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS resolved_at     TIMESTAMPTZ;

-- Partial index used by the sweeper query: only unresolved rows are scanned.
CREATE INDEX IF NOT EXISTS idx_purged_conversations_unresolved
    ON purged_conversations (purged_at)
    WHERE resolved_at IS NULL;

COMMIT;
