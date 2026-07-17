# ADR-164 — Email send-action trust model (LUM-229)

**Status:** Draft (exploration) — recommendation for review, not yet implemented
**Created:** 2026-07-14
**Decided by:** `/explore` (Opus 4.8) — email connector evaluate (LUM-170 + LUM-229)
**Linear:** [LUM-229](https://linear.app/lumogis/issue/LUM-229) (project: Capabilities / Plugins; milestone v1.3)
**Builds on:** ADR-163 (connector risk profiling), the Ask/Do permission model (`permissions.check_permission`, `_HARD_LIMITED`), ADR-147 (privacy mode)
**Evaluate:** `lumogis-devtools/cursor/explorations/LUM-170-229-email-connector-evaluate.md`

---

## Context

An email management connector (LUM-229) exposes tools that *act* on a user's mailbox — draft, sort, label, and **send**. Sending an email is an **irreversible external write of personal data to third parties**: the one action in the email surface that cannot be undone and can cause real-world harm (wrong recipient, premature send, agent hallucinating a commitment). The other tools (read, draft, label, move) are safe or reversible. This ADR fixes the trust model for the dangerous ones so the connector can be built without re-litigating it per tool.

The existing gates this must compose with:
- **`permissions.check_permission`** (`permissions.py:97`) — the sole action chokepoint; per-connector `ASK`/`DO` mode + `is_write`.
- **`_HARD_LIMITED`** (`routes/actions.py:29`) — action types that can **never** auto-elevate to routine "Do": `financial_transaction, mass_communication, permanent_deletion, first_contact, code_commit`.
- **ADR-163** connector risk profile — `always_ask_actions`, `requires_trust_tier`, `data_sensitivity`.

## Decision (recommended)

**Sending is hard-limited and always-Ask; everything else on the email connector is safe-or-reversible and follows normal mode gating. Draft-only is the default posture until the user explicitly elevates.**

1. **`send_email` is a `_HARD_LIMITED` action type.** Add `email_send` to `_HARD_LIMITED` (or classify it under the existing `mass_communication`/`first_contact` floor). Consequence, by the existing machinery: it can **never auto-execute**, never be routine-elevated by the count-to-15 path, and always raises an Ask proposal — even when the connector is in DO mode. There is **no configuration that makes the agent send email without a per-message human approval.**

2. **Draft-only default.** The connector ships in a **draft-only** posture: `draft_reply`/`draft_new` write a local draft (nothing leaves the machine); `send_email` is disabled until the user explicitly enables sending. This is the "safe default until the user elevates trust" the ticket asks for, and it maps to ADR-163's connector profile: **email-send connector = `data_sensitivity: critical`, `writes_externally: true`, `requires_trust_tier: delegate`, `always_ask_actions: [send_email]`.**

3. **Reversible actions follow normal gating.** `move_email`, `label_email`, `mark_read` are reversible writes → Ask by default, DO-elevatable per connector mode, with a reverse token (unmove/unlabel). `read_email`/`search_email` are read-only. `draft_*` are writes that produce only a local artifact → Ask default, safe.

4. **Send requires an explicit, auditable scope.** `Mail.Send` (Graph) / SMTP-send capability is a **separate credential grant** from read — a read-only credential (or app-password scoped to IMAP only) cannot send. The send scope is recorded in the credential audit and surfaced in the connector manager (LUM-333).

5. **Every send is audited irreversibly-flagged.** `send_email` writes an `audit_log` row with `is_reversible=false` and the recipient/subject summary (no full body in the redacted stdout mirror, per `audit.py`). A sent message cannot be un-sent; the audit is the record.

## Why hard-limited rather than "DO-mode auto-send"

The Ask/Do model would, in DO mode, let a write auto-execute. For send that is unacceptable: the failure is irreversible and externally visible. `_HARD_LIMITED` exists precisely for this class (`mass_communication`, `first_contact` are already there) — routing `send_email` through it reuses a proven, tested floor rather than inventing an email-specific exception. It also composes correctly with ADR-163's capability-derived floor: a connector that declares a `send` tool is forced to `writes_externally`/critical regardless of what its manifest claims.

## Alternatives considered

- **Send as a normal DO-elevatable write** — rejected: irreversible external side-effect; the whole point of the hard-limit floor.
- **Per-recipient allowlist auto-send** ("auto-send to my own family") — deferred: a plausible v2 trust-elevation, but it needs the LUM-131 classifier and a recipient-trust model; v1 is always-Ask.
- **A bespoke email-approval flow** separate from Ask/Do — rejected: the existing approvals surface (`routes/api_v1/approvals.py`) already renders pending actions; send proposals slot in as hard-limited pending items.
- **Draft-and-send in one tool** — rejected: keep `draft_*` (safe) and `send_email` (hard-limited) as distinct tools so the dangerous verb is always explicit and separately gated.

## Consequences

- **Easier:** send safety is guaranteed by an existing, tested mechanism; no new approval UI; composes with ADR-163 and the LUM-131 classifier (which reads `always_ask_actions` as a prior).
- **Harder / watch:** `_HARD_LIMITED` is currently duplicated in `routes/actions.py:29` and `actions/executor.py:37` — adding `email_send` must touch both (or, better, dedupe them into one source of truth first). Draft-only default means the connector is deliberately less capable out of the box — an onboarding affordance must explain how to enable sending.

## Dependencies

- **ADR-163 (connector risk profiling)** — email-send is the archetypal `critical` connector; `send_email ∈ always_ask_actions`. This ADR is a concrete instance of that model.
- **LUM-131 (per-action classifier)** — consumes `always_ask_actions`; must never be able to elevate a hard-limited type.
- **LUM-333 (connector manager)** — surfaces the send-scope grant and the draft-only/send-enabled state.
- **Privacy (ADR-147)** — draft *generation* from a natural-language instruction must respect the local-only body pin (see the evaluate) when the source thread is email content.

## Revisit conditions

- **Recipient-trust auto-send** — if households ask for "auto-send to known family contacts", revisit as a LUM-131-classifier-driven elevation over a recipient allowlist (never a blanket DO).
- **`_HARD_LIMITED` dedupe** — if the two definitions are unified into one source of truth, update the reference here.

## Status history

- **2026-07-14:** Draft created by `/explore` during the LUM-170+229 email evaluate (Opus 4.8). Recommendation: `send_email` hard-limited + always-Ask, draft-only default, send as a separate auditable scope, reversible tools on normal gating. Awaiting review before planning.
