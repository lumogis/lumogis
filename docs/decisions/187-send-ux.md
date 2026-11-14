# ADR-187: Send UX + Approvals — composer → confirm card → Send / Save-to-Drafts
**Status:** Finalised
**Created:** 2026-07-24
**Last updated:** 2026-07-24
**Decided by:** /explore (claude-opus-4-8)
**Finalised copy:** docs/decisions/187-send-ux.md

## Context
LUM-650 shipped the send **backend** (`email_reply`/`email_compose` → proposal → Approvals → executor) and LUM-651 the RFC-correct threading; the web only received the credential opt-in. LUM-652 is the **surface** that turns "the backend can send" into "the user can send from Lumogis": a reply/compose affordance on a flagged email → an (optionally LLM-suggested) editable draft in a composer → an Approvals confirm card ("Send this to X, from your address?") with **[Send]** (SMTP) and **[Save to Drafts]** (IMAP `APPEND`) — default Send — plus the first-contact notice.

Three concrete codebase facts frame the decision:
- `ApprovalsPage.tsx`/`RiskBadge.tsx` render `action_log` **denials**, not pending `action_proposals` — there is no web surface today that lists a pending `email_send` proposal or shows a send-confirm card.
- `services/action_proposals.py::list_pending` / `PendingProposalItem` returns `proposal_id/action_name/payload/status/created_at` but **not** the `annotation` (first_contact) — so a confirm card cannot render the first-contact badge without a small additive DTO change.
- **No `\Drafts` APPEND capability** exists; but the seam does (`adapters/email_imap_source.py::_role_folder` does RFC 6154 SPECIAL-USE detection, `_connect_writeback` gives an egress-mapped write connection). LUM-650 shipped `SmtpSender.build_message` **specifically** for this reuse.

## Decision (proposed)
Ship the send surface on the **shipped proposal → Approvals → executor pipeline**, with three coupled choices:

- **Fork A — Save-to-Drafts = a new `email_save_draft` act-tier action (Option A1).** A third write action (`is_reversible=False` — no reverse handler ships; the user deletes the draft in their own client) that builds the **same** MIME as Send (reuse `build_message` + LUM-651 threading; **identical modulo the per-build `make_msgid` Message-ID**) and `APPEND`s it to the SPECIAL-USE `\Drafts` folder with the `\Draft` flag, via a new `EmailImapSource.append_to_drafts(mime_bytes, message_id)` (`_role_folder(client, "drafts")` — the RFC 6154 **role slug**, not a folder name → `client.append(..., flags=["\\Draft"], msg_time=datetime.now(timezone.utc))`, over `_connect_writeback` so an egress block dead-letters). `From` is the IMAP account username, so Save-to-Drafts needs no SMTP opt-in. The confirm card's **[Save to Drafts]** creates+approves+executes this action; **[Send]** executes `email_reply`/`email_compose`. Targeting the SPECIAL-USE folder (not a hand-named "Drafts") avoids Gmail's multi-copy-draft pitfall; a server without a `\Drafts` special-use degrades (`no_drafts_folder`, surfaced via an additive wire `code` field → card offers Send only).
- **Fork B — a dedicated accessible `EmailComposer`** (native `<dialog>` + `showModal()` for built-in focus-trap/Escape, `role="dialog"`/`aria-modal`, first field focused on open) reusing `ChatComposer`'s textarea primitive, with To (compose) / Subject / Body pre-filled for a reply. It is the natural host for LUM-653's voice button. **Not** the raw chat composer (email needs to/subject and a distinct submit → proposal path).
- **Fork C — a minimal core local-LLM reply draft (Option C1).** Reuse the existing **local-first** LLM (same on-box guard as email classification; `context_budget` truncation of the thread) with a simple "draft a reply to this email" prompt; the composer opens **pre-filled but fully editable**, and opens **blank** (graceful) when no model is configured. On-demand only (user clicks reply), never proactive. Proactive pre-drafting + learning-from-corrections stay **app-scope** (LUM-654-adjacent).

Plus one small **additive** DTO change so `GET /api/v1/approvals/proposals` (and `POST …/proposals`) expose the boolean `first_contact` for the card. **No server-side display summary** (recipient/subject) is exposed — the web already holds the reply recipient (the signal's `from_addr`) and the compose form, and deriving a reply recipient server-side pre-ownership would cross the boundary `_annotate_send` deliberately refuses; only the boolean crosses. An additive `code` field on the execute wire response carries the machine failure code (`no_drafts_folder` etc.) for the card's reactive degrade. No new Docker service, no new dependency, no new env var; a migration only if a draft-suggestion cache is wanted (**not** recommended — generate on demand).

## Alternatives Considered
- **Fork A2 — bespoke `POST …/save-draft` route (no proposal).** Simpler (~0.5d) but splits the send/draft flow across two mechanisms, duplicates ownership/egress/threading wiring, and loses the uniform audit trail. The low-stakes ("your own Drafts") argument is real but the asymmetry cost outweighs the saving.
- **Fork A3 — defer Save-to-Drafts entirely.** The ticket names **both** buttons (decision #9); Save-to-Drafts is the escape hatch for users who want to finish in their real client, and LUM-650 shipped `build_message` for it. Dropping it guts half the confirm card.
- **Fork B alt — reuse the chat composer inline** (conflates chat with email send) / **full compose page** (heavier than needed).
- **Fork C2 — ship the composer blank, defer all draft-gen.** Smallest, but under-delivers the ticket's headline ("Lumogis drafts a suggested reply").
- **Fork C3 — proactive background pre-drafting + correction-learning.** Proprietary-intelligence territory, explicitly app-scope, not this chunk.
- **Send without the Approvals confirm** — ruled out: violates the trust model (LUM-650's first-contact gate + "never sends without asking").
- **A separate SMTP "drafts" concept / a Lumogis-local draft store** — ruled out: drafts must live in the mailbox (IMAP) so the user's real client shows them.
- **Rich-text/HTML compose** — ruled out for this chunk (plain-text body only; HTML is a later enhancement with a bigger sanitization surface).

Full detail: `cursor/explorations/LUM-652-send-ux.md`.

## Consequences (anticipated)
**Easier:**
- One **single** send/draft trust path — both Send and Save-to-Drafts are act-tier actions through Approvals, uniformly audited and egress-contained.
- A saved draft is **byte-identical** to what Send would emit (same `build_message` + threading) — the user's client shows exactly what would go out.
- The dedicated composer is a ready host for LUM-653 (voice) and LUM-654 (agentic intent → draft → this confirm/send).

**Harder / foreclosed:**
- Commits to **mailbox-native drafts** (IMAP APPEND), **on-demand** (not proactive) drafting for v1, and **plain-text** compose.
- Proactive pre-drafting + correction-learning are deferred to app-scope.

**Future chunks must know:**
- The `first_contact` DTO exposure is additive and read-only (no new table); LUM-654 can render the same badge.
- A server lacking a `\Drafts` special-use folder degrades to Send-only — document where the card is built.

## Revisit conditions
- **Users want to finish drafts elsewhere frequently** → confirm Save-to-Drafts placement/affordance is discoverable, revisit the composer↔drafts handoff.
- **Draft-suggestion quality/latency becomes a real problem** → revisit Fork C (cache, model routing, or defer to C2).
- **An autonomous/agentic sender (LUM-654) lands** → confirm it routes through the same composer/confirm/action path so the trust surface is not re-implemented.

## Open questions (before /create-plan)
1. **Draft-suggestion scope (Fork C):** C1 (minimal core local-LLM draft, graceful-blank) vs C2 (ship blank, defer). _Recommendation: C1._
2. **Confirm-card location:** a dedicated `SendConfirmCard` at the point of action (composer submit) vs extending `ApprovalsPage`. _Recommendation: dedicated card; ApprovalsPage stays the audit/denials view._
3. **[Save to Drafts] a distinct `email_save_draft` action (A1) vs a `mode` field on the send proposal.** _Recommendation: distinct action._
4. **Expose `first_contact` on the pending-proposal DTO** (additive, read-only) so the card can badge it. _Recommendation: yes._

## Status history
- 2026-07-24: Draft created by /explore (LUM-652).
- 2026-07-24: Finalised by /verify-plan — implementation confirmed the decision (Fork A1 `email_save_draft` IMAP-APPEND to SPECIAL-USE `\Drafts`; dedicated `<dialog>` `EmailComposer` + `SendConfirmCard`; minimal-core on-box `suggest_reply_draft`; additive boolean `first_contact` + wire `code`, no server display-summary). **One deviation** (intent preserved): `create_proposal` keeps its `-> int` return and the create route reads the annotation back via `get_proposal` (owner-scoped), rather than the planned `-> tuple[int, dict]` — the tuple would have broken 7 existing `create_proposal` test call sites the plan hadn't scoped; the read-back is purely additive with identical behaviour (`first_contact` on the create response). **Review refinements landed:** the `EmailComposer` is keyed by signal so state never leaks between emails; Save-to-Drafts success reads "Saved to Drafts ✓" (not "Sent ✓"); draft-gen is bounded by a global concurrency semaphore protecting the shared on-box Ollama; the first-contact annotate recipient lookup is owner-scoped (`signals.user_id`) so the newly-wire-exposed `first_contact` can't become a cross-user oracle. Finalised copy at docs/decisions/187-send-ux.md.
