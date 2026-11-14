# ADR-226: Pausing a source without deleting it (LUM-844)
**Status:** Finalised (canonical copy: `docs/decisions/226-lum-844-source-pause.md`)
**Created:** 2026-11-06
**Last updated:** 2026-11-06
**Decided by:** claude-opus-5-5 (`/explore --headless LUM-844`); finalised by claude-opus-5-5 (`/verify-plan --headless LUM-844`)
**Amends:** [ADR 192](192-lum-728-paperless-mailbox-overlap.md) — the paperless external-consumer query no longer filters on `active`.
**Linear:** [LUM-844](https://linear.app/lumogis/issue/LUM-844/a-source-can-be-deleted-but-not-paused-sourcesactive-exists-in-the)

## Context

`sources.active` has been in the schema from the start, but no route writes it. So the only way to stop a paperless, RSS or page source polling is `DELETE`. That loses the row and its poll cursor, and for paperless it means passing both consent gates again. Several readers already filter on `active = TRUE`: startup scheduling, `page_monitor`, and the paperless and folder-sort guards. They were written while the column could never be false, so making it writable changes them all at once.

The scheduler is an in-memory `BackgroundScheduler`, so jobs do not outlive the process. FastAPI runs the sync PATCH handler in a threadpool, so two edits to one source can interleave.

## Decision

`PATCH /sources/{id}` accepts `active: Optional[bool]` through the existing `_SOURCE_UPDATE_COLUMNS` allowlist. The **column is the only durable state**. Pausing **removes** the `signal_poll_{id}` job (`unschedule_source`). Resuming **adds** a fresh one built from the committed row (`schedule_source`), so edits made while paused take effect, and queues one immediate check so the row's health is current. Every scheduling decision is made by `feed_monitor.reconcile_source_schedule`, which runs after the write has committed and, under one in-process lock, **re-reads** the row and makes the scheduler match the column. The last reconcile to run reads after every committed write, so a concurrent edit cannot bring back a paused job in either ordering. (`UPDATE … RETURNING active`, the mechanism first proposed, closes only one ordering.) Supplying `active` always reconciles, even when the value is unchanged, so re-sending it repairs a schedule a failed call left wrong, and the response reports `schedule_synced`. A reconcile that fails before it could read the row, on a pause or a delete, still removes both jobs (under the lock); one that fails after reading the row as active leaves the existing job rather than killing a source a concurrent resume just started. `poll_cursor` is never written by pause or resume. Paperless re-reads it from Postgres at poll time; RSS and page polls never use it (RSS dedupes by URL, `page_monitor` diffs an in-memory hash).

A paused source keeps its last health error, reads as paused ahead of failing (ADR-215), and still counts against the per-user cap (ADR-217 §5). "Sync now" refuses a paused source with `409 source_paused`, decided under the same lock. Delete pauses the row before its vector purge and marks the source as being deleted (an in-process set guarded by the same lock), so nothing can reschedule or sync it mid-delete: a concurrent resume commits its column write but reconcile still treats the row as paused. If the DELETE itself fails, a non-IMAP row is left paused (the safer leftover for a row the user asked to remove); a mailbox is put back to active, because PATCH cannot resume a mailbox and send, the From picker and backfills all filter on `active`. IMAP rows refuse `active` with `422 pause_not_supported`. A paused paperless source **still counts as an external consumer** for the ADR 192 guard, because paperless keeps consuming mail whether or not Lumogis polls it. This amends ADR 192's `active = TRUE` predicate for the paperless query, and deliberately reverses the intent recorded in the guard's original test ("a source they disabled must not gate them at all"), which was written while `active` could never be false.

## Alternatives Considered

- **`POST /sources/{id}/pause` and `/resume`:** these duplicate PATCH's owner-scoping, no-oracle and race handling, and split source editing across routes after ADR-215 merged it into one.
- **APScheduler `pause_job`/`resume_job`:** the in-memory store loses the pause on restart. A paused job still shows in `get_jobs()`, so `polling_active` would lie. Resume would restart a stale `SourceConfig` and ignore an interval edited while paused.
- **Timed pause (`paused_until`):** needs a migration and a wake-up mechanism for an unstated need. Deferred to the issue's Later section.
- **Skipping inside `_poll_source`:** fails "the job is gone" and adds a read on every tick.

Full detail: `.cursor/explorations/LUM-844-source-pause.md`.

## Consequences

- **Easier:** pause and resume are one click per row. The job list, `polling_active`, startup and `page_monitor` all agree, because they all derive from the column.
- **Harder or ruled out:** a "paused" source cannot signal "I no longer use paperless" to the email guard. That is what delete is for. Persistent APScheduler pause is not used for sources.
- **For future work:** any new reader of `sources.active` must decide whether "paused" means "Lumogis is not fetching" (true) or "the external system is gone" (false). Any new source-edit field joins `_SOURCE_UPDATE_COLUMNS`, and any write that changes a source's scheduled state calls `reconcile_source_schedule` after it commits, never `schedule_source` from a pre-write read. Pausing IMAP is a separate decision: send, the From picker, backfills and the folder-sort guard all assume an active mailbox.

## Revisit conditions

- The scheduler moves to a persistent job store (SQLAlchemy / Postgres). Then reconsider `pause_job` against the column.
- More than one orchestrator process is allowed again (reversing LUM-832). The in-process unschedule then no longer covers every scheduler.
- A user asks to pause a mailbox. Decide the IMAP footprint (send, backfills, organize, folder-sort guard) in its own issue.
- Users ask for "mute for N days". Add `paused_until` to this route, not a verb route.

## Status history
- 2026-11-06: Draft created by /explore
- 2026-11-06: Revised during /review-plan --arbitrate R1 — the race fix is a re-read under one in-process lock, not `RETURNING active` (Decision and Consequences); the poll-cursor claim corrected for RSS/page; added always-reconcile on `active`, `schedule_synced`, the immediate check on resume, and pause-before-purge on delete; named the reversal of the guard test's stated intent.
- 2026-11-06: **Finalised by `/verify-plan`** — implementation confirmed the decision (re-read under one in-process lock; always-reconcile on `active`; `schedule_synced`; immediate check on resume; pause-before-purge; ADR 192 amended). Three refinements found by the implementation-time code review are recorded in the Decision: (a) a `_deleting` mark under the same lock, because pause-before-purge alone let a concurrent `PATCH {"active": true}` re-add the poll job during the purge; (b) a mailbox is restored to active when its DELETE fails, because PATCH refuses `active` on IMAP and a paused mailbox could never be resumed; (c) the `stop_on_error` clean-up runs under the lock and only when the row was not read as active.
