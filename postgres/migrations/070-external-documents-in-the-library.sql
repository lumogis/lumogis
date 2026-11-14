-- SPDX-License-Identifier: AGPL-3.0-only
-- Copyright (C) 2026 Lumogis
--
-- LUM-724 Phase 2 — externally-ingested documents become part of the library.
--
-- Neither `ingest_external_document` (paperless) nor `ingest_email_attachment`
-- wrote a `file_index` row, and `list_documents` reads `file_index` and nothing
-- else. So both were SEARCHABLE BUT NOT BROWSABLE — and, because
-- `purge_document` selects its row from `file_index` and returns early when
-- absent, a paperless document had NO delete path at all. Combined with a strict
-- `added__gt` poll and no deletion propagation, a document deleted in paperless
-- stayed searchable in Lumogis forever. That is what makes this a privacy fix
-- rather than a convenience.
--
-- Additive only. Every column is nullable and needs no backfill to be correct;
-- a separate operator script populates history.
--
-- Rollback (manual; the runner is apply-only / filename-tracked):
--   ALTER TABLE file_index                    DROP COLUMN display_name;
--   ALTER TABLE external_documents            DROP COLUMN purged_at;
--   ALTER TABLE external_documents            DROP COLUMN last_reconciled_at;
--   ALTER TABLE email_attachment_ingest_log   DROP COLUMN purged_at;

-- ---------------------------------------------------------------------------
-- A human name for a document whose path is a URI.
--
-- `DocumentSummary.display_name` is `Path(file_path).name`, which for
-- `paperless://{src}/documents/42` yields `42` and for
-- `email-attachment://{src}/{sig}/0` yields `0`. A library in which every
-- external document is a bare integer is not browsable, which is the entire
-- point of this change.
--
-- NULL keeps the existing `Path(...).name` behaviour, so every uploaded file is
-- unaffected and no backfill is required for correctness.
-- ---------------------------------------------------------------------------
ALTER TABLE file_index
    ADD COLUMN IF NOT EXISTS display_name TEXT;

COMMENT ON COLUMN file_index.display_name IS
    'LUM-724: human-facing name for documents whose file_path is a logical URI '
    '(paperless://…, email-attachment://…). NULL means "derive from the path", '
    'which is correct for every filesystem document. Read by list_documents, '
    'get_document AND the memory search hit renderer — all three derive a name, '
    'and fixing fewer than all three leaves a surface showing a bare integer.';

-- ---------------------------------------------------------------------------
-- Purge marks a verdict row; it must never delete one.
--
-- Deleting it returns the part to `_CANDIDATES_SQL` (which selects
-- `AND l.id IS NULL`) and a matching filing rule re-files it on the next tick —
-- the purge silently undoes itself within fifteen minutes.
--
-- For `email_attachment_ingest_log` the purge sets `reversed_at` AS WELL, in
-- ONE statement. The row then carries two orthogonal facts, each where its
-- reader already looks:
--
--   reversed_at  -> read by uq_..._active (partial on `reversed_at IS NULL AND
--                   skipped_reason IS NULL`); setting it FREES the content-hash
--                   slot, so identical bytes arriving later can still be filed.
--   purged_at    -> read only by application code; `refile_row` refuses a purged
--                   row, so "File again" cannot resurrect a deliberate deletion.
--
-- Encoding both facts in one signal is what defeated the three previous designs.
--
-- `external_documents` gets `purged_at` only: it has no content-slot index to
-- free, and no `reversed_at` column to set.
-- ---------------------------------------------------------------------------
ALTER TABLE email_attachment_ingest_log
    ADD COLUMN IF NOT EXISTS purged_at TIMESTAMPTZ;

ALTER TABLE external_documents
    ADD COLUMN IF NOT EXISTS purged_at TIMESTAMPTZ;

COMMENT ON COLUMN email_attachment_ingest_log.purged_at IS
    'LUM-724: the user destroyed this document. Distinct from reversed_at, which '
    'means "undone and restorable" — a purge sets BOTH, and refile_row refuses '
    'rows where this is non-NULL. Not merged into reversed_at because the two '
    'answer different questions for different readers (index predicate vs '
    'application logic), and merging them is what broke three earlier designs.';

COMMENT ON COLUMN external_documents.purged_at IS
    'LUM-724: the user destroyed this document. The row is retained so the '
    'content_hash replay guard survives a poll-cursor reset.';

-- ---------------------------------------------------------------------------
-- Reconciliation bookkeeping (Phase 4 reads it; Phase 2 only creates it).
--
-- The deletion sweep must walk the whole library over time. Ordering by
-- `updated_at` cannot do that — it only moves when a document's CONTENT
-- changes, i.e. almost never — so the sweep would re-check its first N rows
-- every tick and never reach the rest. The deletions it exists to catch would be
-- precisely the ones it never looked at.
--
-- Ordered `NULLS FIRST`, so never-checked documents are always examined before
-- re-checks.
-- ---------------------------------------------------------------------------
ALTER TABLE external_documents
    ADD COLUMN IF NOT EXISTS last_reconciled_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS external_documents_reconcile_idx
    ON external_documents (user_id, last_reconciled_at NULLS FIRST);

COMMENT ON COLUMN external_documents.last_reconciled_at IS
    'LUM-724 Phase 4: when this document was last checked against its upstream. '
    'Ordering the sweep by updated_at instead would never advance past the first '
    'page, because updated_at only moves on a content change.';

-- No index change on uq_email_attachment_ingest_active, deliberately. Purge
-- frees the content slot by setting `reversed_at`, which the existing predicate
-- already handles. Widening that predicate to mention `purged_at` would also
-- work and is the worse option: it rewrites an index three shipped behaviours
-- depend on, to achieve what one extra column write achieves for free.
