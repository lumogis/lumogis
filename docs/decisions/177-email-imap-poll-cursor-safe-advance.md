# ADR 177: IMAP poll cursor safe advance on processing failure
**Status:** Finalised
**Created:** 2026-07-19
**Last updated:** 2026-07-19
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-07-19 (Composer)
**Plan:** none — shipped before formal plan / verify cycle for this chunk
**Exploration:** `.cursor/explorations/email_imap_poll_cursor_safe_advance_retro.md`
**Draft mirror:** `.cursor/adrs/email_imap_poll_cursor_safe_advance.md`

## Context

ADR-174 (LUM-170) introduced email IMAP polling through `feed_monitor` with `sources.poll_cursor` shaped as `{uidvalidity}:{last_uid}`. A Cursor agent investigation (`origin/cursor/critical-bug-investigation-81f7`) found that both metadata and full-ingest email poll paths persisted `adapter.result_cursor` unconditionally after the processing loop, even when `process_signal` (or full-ingest `ingest_external_document`) failed for an intermediate UID. Because later UIDs in the same batch could still advance the stored cursor, failed messages were **permanently skipped** on subsequent polls.

Paperless ingest already uses a hard-stop pattern in `feed_monitor`; this retro aligns IMAP email with that correctness contract.

## Decision

Email IMAP poll handlers (`_poll_email_source`, `_poll_email_ingest_source`) persist a **safe cursor** derived from the highest UID that was either skipped as a duplicate or fully processed in the current batch — not the adapter's batch high-water mark when an earlier UID failed.

### As-implemented surface

- **`safe_uid`:** starts at the parsed `last_uid` from the stored cursor; advances on duplicate skip or successful processing.
- **Hard stop:** `process_signal` exception (metadata) or `process_signal` / `ingest_external_document` exception (full-ingest) breaks the loop without advancing `safe_uid` for the failed UID or any subsequent UID in the batch.
- **`_email_poll_cursor_for_uid(adapter.result_cursor, safe_uid)`:** rebuilds `{uidvalidity}:{safe_uid}` for `_persist_poll_cursor`.
- **Tests:** `orchestrator/tests/test_email_poll_cursor.py` pins metadata-mode stop-on-failure and duplicate-skip advance.

## Alternatives considered

- **Continue persisting `adapter.result_cursor`:** rejected — drops failed messages silently (pre-ship behaviour).
- **Per-UID dead-letter queue with skip-and-continue:** not chosen at ship time; would need explicit DLQ schema and operator UX.
- **Retry failed UID in-batch before break:** not implemented; next poll interval provides natural retry.

## Consequences

- Transient processing errors no longer cause permanent message loss.
- A persistent failure blocks cursor advance at that UID until resolved (intentional; error logs repeat each poll).
- Future monotonic-cursor sources should follow the same safe-high-water pattern.

## Revisit conditions

- Introduction of a per-UID dead-letter or idempotent upsert that makes skip-and-continue safe.
- Change to unordered IMAP fetch semantics (would invalidate UID monotonic assumptions).

## Linear linkage (Product OS)

- **Existing issue:** none at ship time
- **New issue needed:** yes — retro audit for critical correctness fix merged from Cursor branch review
- **Suggested backfill title:** `IMAP poll cursor must not advance past failed signal processing`
- **Suggested scope:** closure evidence for merge `f56464909` / ADR-177; label `area:signals`, type bug/fix
- **Evidence:** `dev` @ `6849cbfe7` (includes merge); tests `test_email_poll_cursor.py`

## Testing retrospective

- **Added:** `orchestrator/tests/test_email_poll_cursor.py` (2 tests).
- **Run:** `pytest tests/test_email_poll_cursor.py` — **2 passed**.
- **Gaps:** full-ingest stop-on-ingest-failure not unit-tested (P3 optional).
- **Strategy doc:** no update required.

## Status history

- 2026-07-19: Finalised by /record-retro (retrospective).
