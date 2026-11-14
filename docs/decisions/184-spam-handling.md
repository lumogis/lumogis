# ADR-184: Spam handling for email cleanup — provider-verdict-first, reversible actions

**Status:** Finalised
**Created:** 2026-07-23
**Last updated:** 2026-07-23
**Decided by:** /explore (claude-opus-4-8); finalised by /verify-plan (LUM-646)
**Finalised copy:** docs/decisions/184-spam-handling.md

## Context

LUM-646 (Inbox cleanup, chunk #5 of the LUM-641 email vision) must offer suggested bulk actions on a messy inbox, including moving spam/junk out. The false-positive cost is high (a misclassified bank statement in Trash destroys trust) and the actions are destructive, so the detection strategy and safety model must be fixed before LUM-646 is planned. This exploration (LUM-647, fork B) is the explicit gate on LUM-646.

Constraints that shaped the option space: local-first (no cloud filter, no new heavyweight service), the self-hoster/NUC persona, and the hard rule that destructive email actions must be reversible and never silent. Critically, **every mailbox Lumogis connects to has already been filtered by a mature provider-side spam filter** (Gmail/iCloud/Fastmail/Outlook), and the provider's `\Junk` special-use folder is already detected by shipped code (`imap_transport._role_for`).

## Decision

Lumogis will **not build or host a spam filter.** Spam handling is a **hybrid** built entirely on shipped primitives:

- **Primary spam signal:** the provider's own `\Junk`/`\Spam` special-use folder (RFC 6154, detected by `imap_transport._role_for`). Its contents are treated as provider-confirmed spam; Lumogis never re-adjudicates them.
- **Secondary hint:** the existing in-process bulk-header heuristic (`email_classifier._has_bulk_header` → `promotional_bulk`), used **only** for non-destructive actions (label / archive). Bulk ≠ spam, so it never justifies a destructive action.
- **Only destructive action:** move to the provider's `\Trash`-role folder, executed via the shipped `email_organize.apply_organize(op="move", …)` and recorded in the `email_organize_log` ledger (migration 060) — **fully reversible** via `undo_tick` / `reverse_writeback`. Never EXPUNGE, never silent-delete.
- **Confirmation model:** batch-approve per date-range chunk with a per-item expandable list and a prominent chunk-level Undo, mirroring LUM-645's `EmailOrganizeSettings` undo affordance. Per-mailbox opt-in, default off.

This is core-scope (AGPL), adds no Docker service and no new dependency, and lives as a thin cleanup service + LUM-646 UI over existing adapters.

## Alternatives Considered

- **Provider `\Junk` signal alone** — correct core but leaves bulk mail unhandled; the hybrid adds the non-destructive bulk lane. See exploration.
- **Bulk-header heuristic alone** — bulk ≠ spam; destructive use would produce the false positives the ticket warns against. Retained only as the non-destructive secondary hint.
- **Rspamd + IMAP bridge** — heavyweight second filter behind the provider; new Docker service + volume; wrong for local-first self-hosters.
- **SpamAssassin** — dominated by Rspamd (~10× slower, Perl); same redundancy objection.
- **In-process ML (Naive Bayes / TF-IDF)** — cold-start false positives on a fresh install; re-derives the provider's verdict with less data.
- **Silent auto-delete / EXPUNGE** — rejected outright; violates the reversibility constraint.

Full detail: `.cursor/explorations/LUM-647-spam-handling.md`.

## Consequences

**Easier:**
- Highest-quality spam signal is free and already detected; no model, no training, no service, no new dependency.
- Every destructive action is reversible through a ledger the household already trusts (LUM-645), so the safety story is inherited, not rebuilt.
- False positives are structurally minimized: Lumogis only acts destructively on the *provider's* spam verdict, never on its own guess.

**Harder / foreclosed:**
- Lumogis is committed to *consuming* provider spam verdicts, not *computing* them. A mailbox on a self-hosted IMAP server with no upstream spam filter gets bulk labelling but no spam-specific destructive suggestions (graceful degradation when `\Junk` is absent).
- Destructive actions are bounded to move-to-Trash; any future need for harder actions is a separate decision.

**Future chunks must know:**
- LUM-646 implements this; its bulk-action UI and undo must reuse `email_organize_log` + `EmailOrganizeSettings`-style batch undo, not a bespoke path.
- The move-to-Trash undo horizon is bounded by the provider's Trash auto-purge (~30 days on Gmail); this must be disclosed to the user at confirm time.

## Revisit conditions

- **A supported target provider stops advertising `\Junk` via SPECIAL-USE**, or a significant fraction of users run self-hosted IMAP with no upstream spam filter → revisit whether an in-process detector (Option 5) is warranted for those mailboxes specifically.
- **Users report the provider's spam folder is materially worse than a second-pass filter would be** (measured, not assumed) → reconsider Rspamd-as-opt-in for power users.
- **A requirement emerges for spam scoring inside INBOX** (not just acting on the provider's quarantine) → separate exploration; do not extend this decision silently.

## Implementation notes (LUM-646)
The decision shipped exactly as recorded, via `services/email_cleanup.py` + migration 061:
- **Provider `\Junk`-first, trash-only-destructive:** the spam pass walks the provider's own
  `\Junk` special-use folder (adapter `enumerate_role_folder` + `_role_folder`) and the only
  destructive op is a move to the `\Trash`-role folder (`apply_organize(op="trash")`); the
  window pass never trashes INBOX mail. `no_trash_folder` degrades gracefully.
- **Reversible via the LUM-645 ledger, job-scoped:** cleanup-written `email_organize_log` rows
  carry a `cleanup_job_id`, so a job-scoped undo reverses only that job's rows — never a
  standing-organize row (the correctness fix from plan review R1).
- **Batch-approve + bulk = non-destructive:** the window pass auto-labels (non-destructive)
  and only *moves* on user approval; move-to-Trash is confined to the provider-spam pass.
- Window and spam jobs are mutually exclusive per mailbox; move-to-Trash is reversible until
  the provider clears Trash (disclosed in the UI).

## Status history
- 2026-07-23: Draft created by /explore (LUM-647).
- 2026-07-23: Finalised by /verify-plan (LUM-646) — implementation confirmed the decision (provider-verdict-first, trash-only-destructive, reversible via an attributed ledger). Finalised copy at docs/decisions/184-spam-handling.md.
