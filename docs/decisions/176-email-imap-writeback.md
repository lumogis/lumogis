# ADR-176: Email IMAP mailbox write-back of classification labels (act tier)

**Status:** Finalised
**Created:** 2026-07-18
**Last updated:** 2026-07-19
**Decided by:** `/explore` → `/review-plan` (Fable R1 + Sonnet R2) → `/implement` (Opus 4.8) — LUM-623
**Linear:** [LUM-623](https://linear.app/lumogis/issue/LUM-623) (child of LUM-513; project Capabilities / Plugins; milestone v1.3)
**Builds on:** ADR-164 (email send-action trust model, LUM-229), ADR-174 (email IMAP signal source, LUM-170), ADR-163 (connector risk profiling), ADR-175 (user-editable taxonomy)
**Draft/source:** `.cursor/adrs/LUM-623-email-imap-writeback.md`; exploration `.cursor/explorations/LUM-623-email-imap-writeback.md`

## Context

LUM-170 ships read-only IMAP ingest + on-box classification (each email → a `signals` row with a `category` slug and a dedup URL `imap://{source_id}/{uidvalidity}/{uid}`). The "act" tier writes that classification back to the mailbox — mark read, flag, apply a label, or move/archive a specific message — under human approval. **ADR-164 fixed the trust model**: reversible mailbox writes (`label`/`move`/`mark_read`) are Ask-by-default, DO-elevatable, reverse-token, audited; `send_email` is hard-limited and **out of scope** here. The act-tier framework existed (ActionSpec, executor, permission gate, SKIP-LOCKED proposal queue, execute route, multi-instance IMAP credential layer), but this is the **first mutating connector action** and the Ask/Do **proposal producer** (enqueue → approve) did not exist.

## Decision

Mailbox write-back is a set of **in-process reversible-write `ActionSpec` actions** on the `imap` connector (`imap_mark_read`, `imap_flag`, `imap_move`, `imap_label`), reusing `email_credentials.load_connection(user_id, instance=source_id)` + `imapclient` and the executor → `check_permission` → `write_audit` + reverse-token path (ADR-164's Ask/Do model unchanged). The client passes only a **`signal_id`**; the handler recovers `(source_id, uidvalidity, uid)` from the signal url and requires the **source to be OWNED by the caller** (ownership, not visibility). Moves use **UID MOVE (RFC 6851)** with a **COPY + STORE \Deleted + targeted UID EXPUNGE** fallback under UIDPLUS (never a bare EXPUNGE); flags via STORE; "label" is Gmail `X-GM-LABELS`, else a custom IMAP keyword, else a folder-move prompt. A connector-agnostic **proposal producer** (create `pending` → approve → the existing execute route) completes the Ask/Do flow. **Per-message v1.** Never auto-write.

## Framework composition (the load-bearing decision)

The two review rounds proved the framework encoded ADR-164's *policy* but was missing the *mechanics* the first real action needs. LUM-623 lands these as small, backward-compatible, reusable-by-every-future-action edits:

1. **Handler identity.** The executor passes `user_id` to the handler (guarded `try: handler(input, user_id=…) except TypeError: handler(input)` — the established codebase fallback, no handler churn). Identity is **always** executor-supplied, never a payload field; client payloads are `extra="forbid"`. This is the write-isolation guarantee.
2. **Reverse payload.** `ActionResult.reverse_input` is persisted into the audit `reverse_action.input`, so `attempt_reverse` runs with real input. **Reverse relocation is by `Message-ID`** (fetched live at apply time; `Signal.headers` is transient/not persisted), stable across UIDVALIDITY, not COPYUID.
3. **Ask/Do composition — forward AND reverse.** An approved per-message proposal satisfies ASK for a reversible write (`check_permission(approved=True)`), threaded from **both** `claim_and_execute_proposal` and `attempt_reverse` (a token-authenticated reverse of one's own audit row is itself an approval). Hard-limited types are never relaxed. `ActionSpec.input_model` lets the generic producer validate each action's payload. `proposal_queue.dead_letter` + `PermanentActionError` terminate a permanent failure (stale UID, unsupported capability) on the first attempt instead of retrying.

## Alternatives Considered

- **OOP capability tool** — rejected: can't reuse the in-process IMAP connection.
- **Separate action-routing table / standing category→action rules (C1)** — deferred to a follow-up on the LUM-629 taxonomy.
- **Auto-apply on classification** — rejected (ADR-164, no auto-write).
- **SMTP send/draft/reply** — out of scope (LUM-229 `send_email`, hard-limited).
- **New IMAP library** — rejected: `imapclient` (already a dep) covers move/copy/expunge/flags/gmail-labels.

## Consequences

- **Easier:** no new trust surface or approval UI; reversibility rides the existing reverse-token path; the target message + category come free from the signal url. The producer + six framework edits are the reusable template for every future act-tier action.
- **Harder / watch:** the IMAP adapter gained its first read-write path; real-server behaviour (Dovecot + Gmail: MOVE/UIDPLUS detection, X-GM-LABELS vs keyword, Message-ID reverse) is validated in the operator's environment before public release (no live IMAP in CI/build). UIDVALIDITY changes are handled as permanent failures.
- **Future chunks:** bulk write-back, standing rules (C1), the web "do this" affordance, and a `reject` verb are separate follow-ups.

## Revisit conditions
- **Standing automation** ("always archive promotional_bulk") → revisit as a stored rule (C1) with its own approval semantics — never a blanket auto-write.
- A target server lacking both MOVE and arbitrary keywords → folder-move-only for that server class.
- A second act-tier consumer needing a different producer contract → extract the producer to shared infra.

## Status history
- 2026-07-18: Draft created by `/explore` (LUM-623).
- 2026-07-18: Revised during `/review-plan --arbitrate R1` (Fable) — added the three framework-composition decisions (handler identity, reverse-input carrier, approved-satisfies-ASK); reverse relocation switched from COPYUID to Message-ID.
- 2026-07-18: Extended during `/review-plan --arbitrate R2` (Sonnet) — the reverse-execute path also threads `approved=True`; added `proposal_queue.dead_letter` + `PermanentActionError` for permanent failures; live Message-ID FETCH (the transient `Signal.headers` cannot carry it); `ActionSpec.input_model` for generic producer validation.
- 2026-07-19: **Finalised by `/implement` + `/verify-plan`** — implementation confirmed the decision; the six framework edits landed backward-compatible (existing framework/permission suites green) and the write-isolation + ASK-composition + dead-letter invariants are covered by tests (`test_imap_writeback.py`, `test_api_v1_proposal_producer.py`, `test_action_proposals_producer.py`). Real-server IMAP validation deferred to the operator environment (before public release).
