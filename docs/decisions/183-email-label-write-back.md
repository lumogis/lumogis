# ADR-183: Label write-back — systematic, opt-in mailbox organization

**Status:** Finalised (with deviations recorded)
**Created:** 2026-07-22
**Last updated:** 2026-07-23
**Decided by:** /explore LUM-645 (Sonnet 5)
**Finalised copy:** docs/decisions/183-email-label-write-back.md

## Context
Lumogis reads and classifies mail into an internal `category` slug (LUM-629) but never touches the real mailbox. LUM-645 (chunk #4 of the LUM-641 arc) bridges that: apply the classification as visible organization in the user's mailbox — Gmail labels / IMAP keywords, and an optional folder sort — under two independent, reversible, per-mailbox opt-ins (LUM-641 Fork A). The mechanical IMAP write layer already exists (LUM-623: `apply_writeback`/`reverse_writeback`, Gmail `X-GM-LABELS` / keyword fallback / `MOVE`, reversal by Message-ID); the gap is the *systematic application* layer, its audit/reversal, and honest consent UX. A hard constraint surfaced in research: non-Gmail IMAP keywords are frequently **invisible** in common clients (Apple Mail/iCloud show only `$MailFlagBitN` color bits), so the label experience is asymmetric across providers.

## Decision
Build a **direct auto-organize execution path** (`services/email_organize.py`) that reuses the LUM-623 adapter writes and records every applied write in a purpose-built **`email_organize_log`** ledger (idempotency + bulk reversal + activity log). Trigger it **go-forward** from a post-persist hook in `signal_processor` for email sources with an opt-in on (fired in the background so ingest never blocks on an IMAP write), and provide a **`reconcile_source(since, before)`** batch pass for the retroactive/opt-in-time backfill (which LUM-646 later drives). Keep the non-destructive **label** opt-in and the destructive **folder-sort** opt-in as separate consents; never silently move `other`. Opt-in state + opt-in columns live on `sources` (additive migration, keyed on the source PK, mirroring LUM-644's migration 059). Reversal reuses `imap_writeback_reverse`; bulk-undo iterates the ledger. Organization is strictly one-directional (Lumogis → mailbox) — never a two-way label sync.

## Alternatives Considered
- **Auto-approved LUM-623 proposals** — one `action_proposals` row + worker pickup per email is too heavy for bulk, and "auto-approved Ask" is the wrong trust semantic. See exploration.
- **Batch reconcile job only** — labels lag classification; wrong as the primary go-forward path (its reconcile job is folded into the recommendation for backfill).
- **Rebuild IMAP write mechanics / two-way label sync** — ruled out; LUM-623 is proven and LUM-644 already fixed drift as by-design (Lumogis labels are Lumogis's own view).

Full detail: `.cursor/explorations/LUM-645-label-write-back.md`.

## Consequences
- **Easier:** the mailbox reflects Lumogis's classification promptly and reversibly; LUM-646 (chunked cleanup) drives `reconcile_source` + bulk actions on top; ingest stays cheap (one IMAP write, no queue row per email).
- **Harder / committed:** a second write-audit path (the ledger) alongside `action_proposals` — the ledger is the dedupe + reversal source of truth and must never double-apply. Folder-sort is gated behind its own opt-in and never moves `other`.
- **Known asymmetry:** first-class labels on Gmail; on non-Gmail the "label" is a possibly-invisible keyword — resolved by an open product question (caveat vs steer-to-folders vs disable), *not* silently shipped.
- **Future chunks must know:** organization is one-directional; triage state stays Lumogis-only (never written to the mailbox).

## Revisit conditions
- If a `label_visible` per-provider decision (open question #4) lands on "detect client support," revisit whether a detection follow-up ticket is warranted.
- If LUM-646 needs an execution entrypoint the ledger/reconcile API doesn't expose, revisit the `email_organize.py` surface (additively).
- If a future requirement needs mailbox→Lumogis label sync, this one-directional decision must be explicitly reopened.

## Status history
- 2026-07-23: **Finalised** by /verify-plan — implementation confirmed the decision. Deviations recorded: (1) opt-ins applied via a dedicated `PATCH …/organize` route rather than the create INSERT (lower blast radius, same per-mailbox outcome); (2) the reconcile driver is a **standing ledger-driven per-tick sweep** (not a one-shot state machine — the R2 refinement) with undo tick-driven too; (3) namespacing/atom/folder-creation live in a dedicated `apply_organize` adapter method (LUM-623's `apply_writeback` untouched); (4) batched single IMAP session per sweep (review follow-up pulled into v1 to avoid provider lockout). Persistence blocker resolved upstream (LUM-676). Deferred to follow-ups: real-PG reconcile integration test, Playwright e2e, final per-mailbox page mount.
- 2026-07-22: Draft created by /explore
- 2026-07-23: /review-plan --arbitrate R2 (Sonnet 5 critique) — no decision reversal. Refined the reconcile driver from a one-shot state machine to a **standing, ledger-driven per-tick sweep** (watermark `organize_since`; apply-to-existing widens it), and made **undo tick-driven (202)** too — resolving two internal contradictions R1 introduced. Confirmed a second facet of the persistence blocker (Qdrant point-id collision) and added LUM-623 to the affected-chunks list. Review sequence (self → Fable → Sonnet) complete; ADR stays Draft (revised) pending the persistence blocker + implementation.
- 2026-07-23: Revised during /review-plan --arbitrate R1 (Fable 5 critique). The decision (direct exec + `email_organize_log` ledger + reconcile) stands, but two premises were corrected: (1) **"reuses the entire LUM-623 layer, no new mechanics" is false** — folder-sort needs an idempotent `ensure_folder`/`create_folder` + server hierarchy-delimiter discovery, and non-Gmail labels need a slug-**atom** keyword form (imapclient does not quote flags); both are now in v1 scope. (2) A **blocking upstream dependency** surfaced: email signals are built with `signal_id=""` against a `UUID PRIMARY KEY` and no test proves they persist — a Pass-0 live-DB verification (and, if broken, a deterministic `uuid5(url)` signal_id fix) precedes this chunk. The ledger stays **url-keyed** either way. Reconcile gains a poll-tick continuation driver (state columns) so apply-to-existing/LUM-646 can finish a mailbox; the promised activity log gains a `GET …/organize/log` surface.
