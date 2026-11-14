# ADR-202: Recovering a failed send — the mailbox, not the app

**Status:** Accepted
**Created:** 2026-08-03
**Last updated:** 2026-08-04 (finalised by `/verify-plan` — implementation confirmed the decision)
**Finalised copy:** `docs/decisions/202-lum-793-failed-send-recovery.md`
**Decided by:** `/explore LUM-793` (claude-opus-5)
**Linear:** [LUM-793](https://linear.app/lumogis/issue/LUM-793/a-failed-send-is-recoverable-only-while-the-composer-is-open-the)
**Relates:** ADR-201 (LUM-762 — made this safe, and created its attachment window), LUM-652 (the "surface, don't re-drive" posture), LUM-742 (import-graph reachability guard), LUM-147 (retention)
**Exploration:** `.cursor/explorations/LUM-793-failed-send-recovery.md`

## Context

A failed send is recoverable only while the composer is open. The message is durable in `action_proposals.payload` from the moment the user presses Review, but `list_pending` filters `status = 'pending'` and a failed send is `dead`, so it appears on no screen. LUM-793 piece 2 stopped Escape discarding it silently; piece 1 is making it reachable.

Two facts shape the option space, and neither is in the ticket.

**The composer cannot be seeded.** `EmailComposerProps` has no `initialBody` / `initialSubject` / `initialTo`. Restoring *into* it means five-plus new props on a component whose `body` already has four writers and an explicit anti-clobber invariant. That is the real cost of the obvious design.

**ADR-201 created an attachment window.** Making failure terminal put `dead` into `TERMINAL_PROPOSAL_STATES`, and `email_attachment_staging.sweep` deletes a terminal proposal's files "promptly, regardless of age" on an hourly tick. Before LUM-762 those files were pinned forever — a leak that change closed. So the fix that made recovery *safe* made its attachments *disposable*: text survives indefinitely, attachments for about an hour.

## Decision

**Recovery writes a draft to the user's mailbox; it does not restore into Lumogis. And where the outcome of a stranded send is in doubt, Lumogis looks it up rather than warning about it.**

Four parts:

1. **Show the body.** The recoverable-sends listing renders the message read-only. Seeing the text is the reassurance; NN/g's guidance is explicit that a user's original input should be preserved and shown, not summarised.
2. **One action: Save to Drafts**, running the existing `email_save_draft` from the stored payload. No composer changes, no new restore mechanism, no state transition — `dead` stays terminal and a fresh draft is written.
3. **Resolve the send outcome from `email_sent_log`.** `_record_sent_log` writes at submission time, inside the handler, before anything can strand the row. So a stranded proposal is reported as ✅ **Sent** (with the time), ⚠️ **Partially sent** (naming who did and did not receive it), ⚠️ **Not sent**, or ❓ **Couldn't confirm** — never as a hedge. **`partially_sent` is not optional**: `_finalize_send` logs the accepted recipients *then* dead-letters on `recipients_refused`, so a sent-log row exists for a send that partly failed. A binary resolver reports it as fully sent and locks recovery shut for the recipients who never got it — the worst outcome this feature can produce. `proposal_id` is added to `email_sent_log` so the match is exact rather than heuristic on subject + recipients + time window.
4. **A 7-day grace period on terminal-proposal attachments**, with the row reading `email_attachment_staged` at render time so it states availability truthfully (`3 files · available until Sat` / `3 files — no longer available`).

**Retention: 30 days listed, row never auto-deleted.** Deliberately distinct from LUM-147's `email_draft: 3 days`, which expires proposals the user never acted on; a failed send is something they *tried* to do.

A new `list_recoverable_sends` query rather than widening `list_pending`, so `ApprovalsPage`'s existing contract is untouched.

## Alternatives Considered

Full detail in the exploration.

* **Restore into the composer** — what the ticket describes. Rejected as the recommendation: largest surface, expands a fragile multi-writer component, still lossy on attachments, and quietly reverses LUM-652's deliberate "surface, don't re-drive" call.
* **Auto-save to Drafts at failure time** — the only option that beats the sweep unaided, and rejected for that reason not being enough: it takes an unrequested IMAP write at the moment the user is looking at an error, in a product whose trust model is "Lumogis proposes, you approve".
* **Read-only body** — honest and tiny, but makes the user the recovery mechanism.
* **Reviving the `dead` row in place** — rejected outright. It would make `dead` non-terminal and reintroduce a transition ADR-201 deleted.
* **Hedged copy on the stranded class** ("this may have been sent") — rejected, and for a stronger reason than style: **the question is answerable.** An earlier draft of this ADR and of ADR-201 both described `stale_executing_claim` as an inherently ambiguous side effect. `email_sent_log` already records the send at submission time, so it is resolvable in all but a millisecond-wide window. Even the "honest fallback" of telling the user to check their Sent folder is worse than looking, when Lumogis is the thing holding the log.
* **A 30-day attachment grace period** — rejected in favour of 7. Attachments are megabytes on a self-hoster's disk and this is a rare event; matching the 30-day listing retention would be a real ongoing cost for a marginal gain.

## Consequences

**Easier**
* **Recovery survives a broken SMTP path — for most failures, not all.** This is the main advantage over restoring into the composer: if the failure was SMTP down, misconfigured, rate-limited, or an app-password revoked on the SMTP side, an in-app restore fails again on the same path, while a draft in the real mailbox can be sent from Thunderbird, a phone, anywhere. In-app restore recovers the *text*; this recovers the *intent*.

  **Qualified after review:** the argument does **not** hold universally. Save-to-Drafts is an IMAP `APPEND`, so where IMAP and SMTP share one credential — an xoauth2 mailbox with a revoked or unrefreshable grant — the draft write fails on the same token that broke the send. The advantage is real for app-password mailboxes and for SMTP-specific failures, and absent for shared-credential OAuth revocation. An earlier draft of this ADR stated it without the qualification.
* Reuses a shipped, tested action that is already the composer's own failure fallback — "the thing you would have clicked, clickable later".
* **A stranded send stops being a mystery.** The user is told whether it went out, with a timestamp, instead of being handed a caveat to resolve themselves.
* `EmailComposer` is untouched.

**Harder**
* **Recovery becomes a mailbox concept, not an in-app one.** A future "resume editing in Lumogis" would need the composer props anyway; this does not build toward that.
* **The attachment grace period re-opens, in bounded form, the leak LUM-762 closed.** That is a real cost and belongs in its own ticket rather than inside a feature chunk.
* A mailbox with no Drafts folder (`no_drafts_folder`) still has no recovery path. The body becomes visible and copyable, which is more than today, but **LUM-793's third acceptance criterion is not fully met** — stated rather than redefined away.
* **`email_sent_log` gains a column.** Small, nullable, but it is a schema change on a table the first-contact classifier reads, so it needs its own migration and care.

**Future chunks must know**
* **Retention is one number, shared with LUM-147.** Two independent answers on one table is how a listing shows rows the archiver has hidden.
* **The new surface must be routed and covered by LUM-742's import-graph guard.** Five email components have already shipped unreachable.
* `error = 'stale_executing_claim'` means **the outcome was not recorded, not that it is unknowable**. Resolve it from `email_sent_log`, which is written at submission time. Do **not** reason from `attempt` — the sweeper does not bump it, so a swept row reads `0` despite having been attempted. (ADR-201 and an earlier draft of this ADR both called this class inherently ambiguous. That was over-stated.)

## Revisit conditions

* **Usage shows people look in Lumogis, not their mail client** — the central bet, held at Medium confidence with no usage evidence. If wrong, Option A is right and the composer cost is worth paying.
* **A second consumer needs a seeded composer** (scheduled send, templates, "edit this again") — the prop expansion stops being this feature's cost alone.
* **The attachment window is measured and differs from the code reading** — the hourly sweep is read from source, not observed on a real stack.
* **`no_drafts_folder` mailboxes become common** — the decision leaves them with nothing.

## Status history

- 2026-08-04: **Finalised by `/verify-plan` — implementation confirmed the decision, with three corrections the ADR did not foresee.** All four parts shipped as decided: read-only body, Save-to-Drafts as the single action, outcome resolved from `email_sent_log`, and a 7-day attachment grace. The `partially_sent` state added at arbitration proved necessary in a way the arbitration had not fully seen — **it cannot be derived by differencing the sent-log row against the payload**, because `email_reply` resolves its recipient from the signal at execute time and carries no `to` at all, so a partly-refused reply would still have read as fully sent. The resolver reads the dead row's `error`, which names the refused set exactly. Two further corrections: `not_sent` was being asserted where **no lookup was possible** (only `email_compose` carries the `source_id` the heuristic keys on), now `unknown`; and the adapter's TOTAL-refusal message contains the partial one as a substring, guarded explicitly. A security review found that `str(ValidationError)` embeds `input_value=`, so a shape rejection was writing message bodies to the orchestrator log — fixed.

- 2026-08-04: **Revised at arbitration R1.** Two blind critics independently found that a partial recipient refusal writes a sent-log row, so the three-state resolver would have reported it as fully sent — `partially_sent` added. The "survives a broken SMTP path" claim is qualified: Save-to-Drafts is an IMAP APPEND, so for a shared-credential xoauth2 mailbox with a revoked grant it fails on the same token. And the all-or-nothing attachment rule is explicitly NOT carried over to drafts — a send that loses a file is unrecallable, a draft is recoverable, so recovery strips expired ids and says so.
- 2026-08-03: **Revised before planning** — all four open questions answered by Thomas, and one **correction**: `stale_executing_claim` was described here (and in ADR-201, and in a comment on LUM-793) as an inherently ambiguous side effect. It is resolvable from `email_sent_log`, which is written at submission time inside the handler. The hardest UX question in the ticket became a lookup. Decision gained the sent-log resolution, the read-only body, 7-day attachment grace, and 30-day listing retention.
- 2026-08-03: Draft created by `/explore` LUM-793. Registered as blocking LUM-147 (🟡 — shared retention policy).
