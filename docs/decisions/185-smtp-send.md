# ADR-185: SMTP send — stdlib smtplib, submission-as-the-user, sync handler

**Status:** Finalised
**Created:** 2026-07-23
**Last updated:** 2026-07-23
**Decided by:** /explore (claude-opus-4-8)
**Finalised copy:** docs/decisions/185-smtp-send.md

## Context
LUM-650 adds the first outbound email capability — reply and compose-new, sent **as the user through their own provider's SMTP submission**, mirroring the inbound IMAP model (never a Lumogis relay, never a home-IP MTA). The parent LUM-641 exploration resolved the product decisions (reply + compose-new; per-account credentials; non-hard-limited actions). This decision is about the **mechanism**: SMTP library, credential model, and egress containment.

The load-bearing constraint is architectural: the action executor (`actions/executor.py::execute`) and every email handler (`actions/handlers/imap_writeback.py::handler`, using `imapclient`) are **synchronous**, and sends run through the proposal queue → Approvals → executor (background, off the request path). The provider registry already ships `smtp_host`/`smtp_port` (LUM-644), and egress guards already exist (LUM-571 ceiling, LUM-625 proxy, `imap_egress_scope`).

## Decision
Send outbound email with the Python **standard-library `smtplib`** (`email.message.EmailMessage` + `email.utils.make_msgid` + `SMTP.send_message`) inside the existing **synchronous** action handler — a direct mirror of the sync `imapclient` inbound adapter, with **no new dependency**.

- **Adapter `adapters/email_smtp_sender.py::SmtpSender`**: connect via the provider registry's `smtp_host`/`smtp_port`, choosing TLS by port (465 → `SMTP_SSL` implicit TLS; 587 → `SMTP` + `starttls()`); `login(app_password)`; build `EmailMessage` (accepting `in_reply_to`/`references` so LUM-651 threading slots in); `send_message`; return the Message-ID. Wrapped in a new `smtp_egress_scope` symmetric with `imap_egress_scope`.
- **Actions `actions/handlers/smtp_send.py`**: `email_reply` + `email_compose` ActionSpecs (`connector="smtp"`, `is_write=True`, `is_reversible=False`, non-hard-limited), routed through the existing proposal queue → Approvals → executor.
- **Credentials**: an `smtp` connector in `connector_credentials`/`email_credentials`, instance-keyed by `sources.id`, `auth_type="app_password"`, host/port prefilled from the registry.
- **Egress**: fold the submission host into the LUM-571 ceiling and LUM-625 proxy allow-union (`declared_smtp_hosts` + `smtp_egress_scope`), symmetric with IMAP.
- **`first_contact` hard-limit**: scoped to autonomous sends only; user-approved compose-new to a new addressee is allowed. **Implementation refinement (verify-plan):** because `email_send` is non-hard-limited, a Do-mode send to a new address would otherwise auto-approve — so `first_contact` is computed at **proposal create** via a new general `ActionSpec.annotate` hook, persisted on `action_proposals.annotation` (migration 062), and read by the Ask/Do resolver to **force Ask** even in Do mode. The hook **fails closed** (uncertainty → Ask). This makes the flag a real auto-approval gate, not merely a UI disclosure.

## Alternatives Considered
- **`aiosmtplib` (async)** — correct for an async send path, but the executor and handlers are synchronous and sends are backgrounded; it would force event-loop bridging inside a sync handler (or an out-of-scope async executor refactor) plus a new dependency, for no real benefit here.
- **Lumogis relay / home-IP MTA** — terrible deliverability, requires user-owned SPF/DKIM/DMARC/rDNS, breaks send-as-the-user; ticket-forbidden.
- **Third-party transactional relay (SendGrid/Mailgun/Postmark)** — cloud dependency, per-message data egress, not send-as-the-user; wrong trust model.

Full detail: `.cursor/explorations/LUM-650-smtp-send.md`.

## Consequences
**Easier:**
- Zero new dependency; the send adapter is the literal mirror of the shipped sync IMAP adapter, and reuses the provider registry, credential layer, proposal/Approvals/executor pipeline, and egress guards.
- Sending via the user's own provider submission means the provider applies the user's SPF/DKIM/DMARC alignment — the deliverability reason to avoid a relay/MTA.

**Harder / foreclosed:**
- Commits to submission-as-the-user; unattended/bulk send (newsletters, autonomous agents) is a separate, later trust decision (the `first_contact` hard-limit anticipates the autonomous case).
- Keeps the send path synchronous; a future async executor refactor could revisit `aiosmtplib`, but nothing here depends on async.

**Future chunks must know:**
- The `SmtpSender` accepts `in_reply_to`/`references` from day one → LUM-651 supplies them via IMAP re-fetch without changing the adapter signature.
- LUM-652's Send UX consumes the `email_reply`/`email_compose` proposal + Approvals shape defined here.

## Revisit conditions
- **The action executor is refactored to async** → reconsider `aiosmtplib` for symmetry.
- **A supported provider drops app-password SMTP** (as Outlook did — OAuth-only) → that provider needs the OAuth path (LUM-674), same as IMAP.
- **A genuine unattended/bulk-send requirement emerges** → separate exploration + trust review; do not extend the attended send path silently.

## Status history
- 2026-07-23: Draft created by /explore (LUM-650).
- 2026-07-23: Finalised by /verify-plan — implementation confirmed the decision (stdlib smtplib, submission-as-the-user, sync handler, reuse-IMAP credential default, egress folded into the LUM-571 ceiling). Refinement recorded: the first-contact protection is a create-time `ActionSpec.annotate` hook forcing Ask (fail-closed), not just a notification. Finalised copy at docs/decisions/185-smtp-send.md.
