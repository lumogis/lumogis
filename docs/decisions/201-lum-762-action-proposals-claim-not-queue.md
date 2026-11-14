# ADR-201: `action_proposals` is a claim, not a queue — failure is terminal

**Status:** Accepted
**Draft history:** `.cursor/adrs/LUM-762-action-proposals-executor.md`
**Created:** 2026-08-03
**Last updated:** 2026-08-03 (accepted on implementation of LUM-762)
**Decided by:** `/explore LUM-762` (claude-opus-5)
**Linear:** [LUM-762](https://linear.app/lumogis/issue/LUM-762/action-proposals-has-no-background-executor-claim-next-has-zero)
**Amends:** ADR-040 (atomic claim — its drain obligation superseded), ADR-148 (its retry-cap table row), ADR-196 (its "permanence is load-bearing" consequence, now structural)
**Relates:** LUM-100 / LUM-147 / LUM-191 / LUM-407 (blocked by this decision), LUM-793 (failed-send recovery)

## Context

ADR-040 shipped `action_proposals` as a mailbox-pattern queue modelled on `services/batch_queue`: SKIP-LOCKED claim, attempt counter, exponential `run_after` backoff, dead-letter threshold, stale-claim sweeper. `batch_queue` has an APScheduler drain loop. `action_proposals` never got one.

`proposal_queue.claim_next` had **zero production callers** for three releases. The only thing that executed a proposal was the synchronous `POST /api/v1/approvals/proposals/{id}/execute`, and the web composer calls it immediately after `approve` — no surface anywhere in the product approves without executing.

So `_FAIL_SQL`'s re-approve branch wrote rows into a state nothing read: a non-permanent failure set `status='approved'` with a future `run_after`, and the row stayed there forever. Three things followed. `_CLAIM_BY_ID_SQL` did not filter `run_after`, so the field was advisory even on the path that ran. `email_attachment_staging` treats `approved` as a **live** state and never sweeps a live proposal's files, so such a row **pinned its staged attachments on disk permanently** — a present-tense leak, not a latent one. And `EmailComposer.tsx:472` already discarded the proposal id on failure, so the client assumed a death the server did not guarantee.

The hazard was what a later background executor would do: every stranded row becomes an action the user already performed by hand. On the SMTP path that is a duplicate outgoing email, which is not recallable. Not hypothetical — during LUM-757's planning a self-review proposed making transient SMTP failures retryable "because the queue retries with backoff", and it had to be reversed in arbitration.

The real question was never "should we build a worker". It was **what does `status='approved'` mean?** It meant two incompatible things at once — "a human approved this and the client is about to run it", and "this failed and will be retried later" — and only the first had machinery behind it.

## Decision

**`action_proposals` is an atomic claim over a synchronous execution, not a background queue. Failure of an approved proposal is terminal on the first attempt.**

* `fail_execution` and `_FAIL_SQL` are removed. `dead_letter` is the sole disposal, so the state machine is one-way: `pending → approved → executing → done | dead`. There is no `executing → approved` edge and therefore no row a future worker could redrive. **Amended 2026-08-04 (LUM-793):** one further edge exists, `dead → archived`, and it does not weaken this — see Consequences.
* `claim_next`, `_CLAIM_NEXT_SQL`, the exported `CLAIM_NEXT_SQL` and `ACTION_PROPOSALS_MAX_ATTEMPTS` are deleted.
* `_CLAIM_BY_ID_SQL` gains `AND run_after <= NOW()`, so the field is never advisory again. A row deferred to the future returns **409 `proposal_deferred`** with `retry_after`, not a `400 invalid_proposal_state` naming `approved` — the one state that is supposed to be claimable.
* `_DEAD_LETTER_SQL` no longer stamps `run_after`; that column belongs to LUM-147's `NOT_NOW` deferral.
* `claim_by_id`, `mark_done`, `dead_letter` and `reset_stuck_claims` **stay**. The claim is reached on every send and exists for a live household condition — one approved proposal visible to two clients, either of which can press Execute. The sweeper is the only thing that rescues a row stranded by a mid-execution kill.
* Schema unchanged, no migration. `attempt` is written once, by `dead_letter`.
* `ACTION_PROPOSALS_QUEUE_ENABLED` → `ACTION_PROPOSALS_SWEEPER_ENABLED` (legacy honoured with a startup warning for one release), because it gated a sweeper and never gated a queue.

`result.permanent` survives, but only to choose the **error code** the composer renders — it no longer selects between two disposals, because there is only one.

## Alternatives Considered

Full detail: `.cursor/explorations/LUM-762-action-proposals-executor.md`.

* **Build the in-process executor** (mirror `batch_queue`'s tick) — rejected. A draining queue is at-least-once, at-least-once requires an idempotent consumer, and the consumers here are a third-party SMTP server and a third-party IMAP server. There is no key to dedupe on: `build_message` mints a fresh `Message-ID` per attempt, so a redrive is not the same action retried, it is a second distinct email. Making it safe would mean searching the user's Sent folder before every retry. And the demand is empty: nothing approves without executing, and background action execution already exists via `services/routines.py`, which bypasses this table entirely.
* **Delete the machinery wholesale** — rejected as one step too far. It also removes the atomic claim, which is the part of ADR-040 that has always run, and spends a migration to drop `run_after`, which LUM-147 will ask for.
* **An external queue (Celery, River, Windmill)** — rejected. A new Docker service for a self-hoster, to run a queue with no demonstrated demand; already rejected in ADR-040's alternatives.
* **Leave it and add a comment** — rejected. That is what ADR-196 did, and LUM-762 exists because a comment plus one parametrised test did not stop a competent reviewer proposing the retry a second time.
* **Widen the pre-claim check to the DO-mode permission gate** — rejected on inspection. With `approved=True`, `check_permission` (`permissions.py:157-161`) denies only hard-limited actions, which `proposal_execute.py:75-84` already 403s, so the branch is unreachable. Duplicating a *dynamic* gate risks the two copies diverging — the hazard ADR-195/196 keep flagging.

## Consequences

**Easier**

* Every hazard LUM-762 listed is closed **by construction** rather than by a guard. No `approved` row with a pending retry can exist, so no future worker can redrive one into a duplicate send.
* Staged attachments release — a failed send now reaches a terminal state, which is what `email_attachment_staging.sweep` keys on. Those files were previously pinned forever. **Amended 2026-08-04 (LUM-793): "promptly" no longer holds, deliberately.** Terminal-proposal files now survive `EMAIL_ATTACHMENT_DEAD_GRACE_HOURS` (default 7 days) from `finished_at`, because this change created a window it did not anticipate: LUM-793 lists a failed send for 30 days and offers to save it to Drafts, and sweeping its attachments an hour after the failure made that offer a lie. The grace is bounded and **terminal-only** — a live proposal's files are still never swept on age, and an unreferenced file still ages out on the TTL, so the leak this ADR closed stays closed.
* ADR-196's "the send path's permanence is load-bearing and must not be 'fixed'" stops being an instruction a reviewer can talk themselves out of and becomes a property of the schema path.
* A **Restore into composer** on a failed send (LUM-793) becomes safe to build: a terminal `dead` row cannot race a worker, which an `approved` row could.

**Harder**

* **Ask-tier proposals are strictly synchronous and one-shot.** A failure is final until the user builds a fresh proposal. Any future deferred-execution feature — send-at-07:00, approve-from-phone-while-Core-restarts, LUM-147's `NOT_NOW` resurfacing — must add a drain **and** answer the idempotency question first. That is the intended cost.
* **Unknown-action terminality is config-dependent.** `main.py:535-541` registers the action handlers *inside* the `BATCH_QUEUE_ENABLED` gate, so a deployment with the batch queue off has an empty action registry, and every approved proposal returns `Unknown action`. Those rows previously survived as `approved`; now they die. The error string is carried through verbatim so the population is greppable. **Resolved 2026-08-03 by LUM-794** — handler registration is now unconditional, so `Unknown action` means a genuinely absent action rather than a batch-queue misconfiguration. This paragraph is retained as the record of why the coupling mattered.
* **Not every `dead` row is safe to restore-and-resend**, which LUM-793 must respect: `error = 'stale_executing_claim'` covers both a mid-send kill *and* the 503 `bookkeeping_failed` path where the email **did** go out. Those rows also read `attempt = 0`, because the sweeper does not bump it — so the counter cannot distinguish them. Read `error`, not `attempt`.

  **Amended 2026-08-03 (LUM-793 exploration):** this paragraph said the class was ambiguous *and left it there*, which over-stated the problem. The outcome was not **recorded**, but it is largely **recoverable**: `smtp_send._record_sent_log` writes to `email_sent_log` at submission time, inside the handler, before anything can strand the row. A matching sent-log row is positive evidence the message went out; its absence is strong evidence it did not. The genuinely unresolvable window is between `smtp.send_message()` returning and that INSERT committing. LUM-793 resolves the class by lookup rather than warning about it.

**Future chunks must know**

* **There is exactly one terminal→terminal edge: `dead → archived` (LUM-793).** A user dismissing a failed send from the recovery listing moves the row out of the listing without deleting it. It is safe for three reasons that must all stay true, and adding a *fourth* edge is not licensed by this one: `archived` was already a legal status in migration 022's CHECK and already in `TERMINAL_PROPOSAL_STATES`, so no downstream consumer — the attachment sweep above all — behaves differently; the transition runs **no action**, so at-least-once delivery is not in play; and it is strictly terminal→terminal, so nothing becomes claimable.

  **`dead → approved` remains forbidden, and that is the load-bearing part.** It is the edge that would make a failed send re-executable and reopen the duplicate-email hazard this ADR exists to close — an "make it recoverable again" refactor is exactly how it would return. Pinned by `test_dismiss_never_produces_approved`, whose only job is to fail when someone writes it.
* **`approved` is momentary, not durable.** A row outliving its HTTP request is a defect, not a queue.
* **`run_after` belongs to deferral, not backoff.** LUM-147 owns it.
* **No handler may return `ActionResult(success=False)` expecting a retry.** Raise `PermanentActionError` with a code, or let the exception surface — both dispose identically; the code is what the user sees.
* ADR-040's `claim_next` obligation on LUM-100 / LUM-147 / LUM-191 is superseded.

## Revisit conditions

* **A surface ships that approves without executing** — approve-from-push-notification, or one household member approving for another device. The single fact that would restore demand for a drain, and it must arrive with an idempotency answer for the SMTP path.
* **An action lands whose side effect is genuinely idempotent and externally keyed** (a Lumogis-internal write, or a provider API with an idempotency header). The at-least-once objection is specific to unkeyed third-party mail submission.
* **`routines.py` starts executing proposal rows** rather than calling `executor.execute` directly — the two background paths would then need one story.
* **Multi-worker Core** (LUM-407) — the claim's guarantees need re-deriving under N schedulers.

## Status history

- 2026-08-04: **Amended on implementation of LUM-793 piece 1.** The state machine gained one edge, `dead → archived`, for dismissing a failed send from the new recovery listing; recorded above with the reasons it is bounded and the reason `dead → approved` is not. Also relevant to this ADR's "Not every `dead` row is safe to restore-and-resend" paragraph: the resolver LUM-793 built has **four** states, not the three its plan first specified. `_finalize_send` logs the *accepted* recipients and then dead-letters on `recipients_refused`, so a sent-log row exists for a send that only partly went out; a binary resolver reports that as fully sent and locks recovery shut for the recipients who never received it. Two blind critics found it independently.
- 2026-08-03: **Accepted on implementation of LUM-762.** 18 unit + 7 real-Postgres cases. **The security invariant had to change lanes:** cross-user isolation was guarded by a fake-lane test that *passes under its own mutation* — `FakeProposalQueueStore` gates its claim branch on the literal substring `"where id = %s and user_id = %s"`, so dropping the predicate makes the string miss, `fetch_one` returns `None`, and a test asserting `is None` goes green. Demonstrated empirically, then moved to the Postgres lane. Mutations B/C/F were **run**, not written. Two blind critics (fable-5, sonnet-5) independently found three defects in the plan, including a re-pointed race test that would have **hung CI** (`_CLAIM_BY_ID_SQL` has no `SKIP LOCKED`) and a fake with no `_DEAD_LETTER_SQL` branch at all, making `dead_letter` a silent no-op against it. Draft retained at `.cursor/adrs/LUM-762-action-proposals-executor.md`.
- 2026-08-03: Draft created by `/explore` LUM-762. Registered as blocking LUM-100 (🔴), LUM-147 (🟡), LUM-761 (🟡), LUM-407 (🟡), LUM-191 (🟡).
