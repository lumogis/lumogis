# ADR-191: Email unsubscribe — no server-side fetch of sender-supplied URIs
**Status:** Finalised
**Created:** 2026-07-25
**Last updated:** 2026-07-25
**Decided by:** /explore LUM-697, hardened through two critique/arbitration rounds
**Finalised copy:** docs/decisions/191-email-unsubscribe.md

> **Finalisation note (LUM-697):** implemented as decided, with one scoped
> reduction found during review — suppression ships as **`hide` only**.
> `mark_read` and `trash` were cut because `apply_organize`'s op set is
> label/move/trash (`unknown_op` otherwise), `_pending_for_op` cannot retry work
> with no `signals` row, and `undo_tick` reverses only `{label, move}` — all
> three would need building rather than inheriting. Deferred to LUM-709.
> The core decision — never fetch a URI extracted from message content — is
> unchanged and is enforced by a structural test
> (`orchestrator/tests/test_unsubscribe_no_outbound_http.py`) rather than by
> convention. Deferred one-click work is LUM-708, blocked on LUM-707 (inbound
> DKIM) and on extending the LUM-625 CONNECT proxy.

**Status:** Finalised
**Created:** 2026-07-25
**Last updated:** 2026-07-25
**Decided by:** Sonnet 5 (via `/explore LUM-697`)

## Context

Inbox cleanup (LUM-646) sorts a recurring newsletter but never stops it arriving. LUM-697 asks to parse `List-Unsubscribe` / `List-Unsubscribe-Post` at ingest and act on it, including RFC 8058 one-click `https:` execution, "egress-scoped like the rest of the email family, mirroring `smtp_egress_scope`".

Three constraints shaped the option space, all discovered in the codebase rather than assumed:

1. **The destination is attacker-controlled.** Every other outbound connection the email arc makes — IMAP, SMTP submission, OAuth token endpoints — goes to a host the user typed into a settings form. A `List-Unsubscribe` URI is written by whoever sent the message, including whoever sent the spam.

2. **The egress ceiling cannot express a runtime-discovered host.** `services/egress_guard.py::activate_ceiling` calls `tethered.activate(locked=True)` at lifespan startup; scopes compose by **intersection**, and the feature flag's own description says the allowlist is "baked at boot (restart to change)". Every entry in `_ceiling_extra_hosts()` — IMAP hosts, the IMAP proxy, SMTP submission hosts, OAuth token hosts, env-derived infra — is statically enumerable. `smtp_egress_scope` is safe precisely because its host set is user-declared; there is no equivalent declaration for an unsubscribe target, so it cannot be faithfully mirrored.

3. **There is no inbound DKIM verification.** RFC 8058 §3.1 requires the `List-Unsubscribe`/`List-Unsubscribe-Post` headers to be DKIM-signed so a receiver can tell a genuine one-click header from a forged one. `grep -il dkim` across `orchestrator/` returns one file, and only in a comment about *outbound* mail. Without signature verification the RFC 8058 gate is decorative and the destination is fully attacker-chosen.

Comparable products have shipped this and been bitten: Nextcloud Mail took a blind-SSRF-to-internal-network advisory for server-side `List-Unsubscribe` fetching (GHSA-4pp4-m8ph-2999 / CVE-2023-48307), and Horde Webmail took stored XSS from a `javascript:` URI in the same header (CVE-2025-68673).

## Decision

**Lumogis will not fetch a URI extracted from message content server-side.** For unsubscribe specifically: parse and persist `List-Unsubscribe` / `List-Unsubscribe-Post` per `(user_id, source_id, from_addr)` at ingest; act on the `mailto:` form by sending mail through the existing `email_compose` SMTP path and the existing proposal → Approvals → executor pipeline; surface the `https:` form as an `https:`-only link the **user's browser** follows, which is what mainstream mail clients do; and back both with per-sender suppression built on the shipped `imap_writeback` actions, since that is the only mechanism that works when a sender ignores the request. No new egress surface is added.

## Alternatives Considered

- **Server-side one-click `https:` POST with a hardened URL validator** — what LUM-697 literally asks for. Rejected: the destination is attacker-chosen with no DKIM verification to gate it, it is architecturally incompatible with the boot-baked egress ceiling, and it reproduces a pattern two comparable products have already been exploited on.
- **One-click POST routed through a Squid-style CONNECT proxy (the LUM-625 Layer-2 pattern)** — the correct containment shape, and the only option that permits a runtime-discovered host without weakening the ceiling. Deferred, not rejected: ~1.5 weeks and a Docker sidecar to automate a click, and it still leaves forged headers and the confirm-your-address-is-live problem untouched.
- **Generic link-following from the message body** — unbounded fetch of attacker-chosen URLs with no RFC gate. Already ruled out by the ticket itself.
- **Third-party unsubscribe services** — require forwarding mailbox contents to a cloud service; categorically incompatible with the product.
- **LLM-driven navigation of the sender's unsubscribe page** — an LLM acting on a live attacker-controlled page; fails the LUM-699 prompt-injection posture with a much larger blast radius than a string.

Full detail: `cursor/explorations/LUM-697-email-unsubscribe.md`.

## Consequences

**Easier**

- No new egress surface, so `EGRESS_GUARD_CEILING` stays a meaningful strict mode and the LUM-571 ceiling is not weakened for every other subsystem to serve one feature.
- The `mailto:` path inherits first-contact protection, Approvals review, and the audit trail from the shipped send stack rather than needing its own.
- Parse-and-store is cheap: `List-Unsubscribe` is already in `_HEADER_FIELDS` and already read as a boolean by `email_classifier._has_bulk_header`. Only the value was being discarded.
- The human stays in the loop for exactly the judgment the security guidance says a human should make — whether this sender is trustworthy enough to touch.

**Harder / impossible**

- Not "one click" in the RFC 8058 sense for `https:`-only senders — the user gets a browser tab, not a silent completion.
- No unattended or routine-driven unsubscribe for the `https:` path; it needs a browser and a person.
- Bulk over many `https:`-only senders means many tabs. Sender suppression is the answer there, not unsubscribe.
- Coverage depends on the `mailto:`/`https:` split in real mail, which the 2026 bulk-sender rules pushed toward `https:`. This is measurable and should be measured before the UI is designed.

**Future chunks must know**

- Server-side fetching of message-derived URIs is closed by decision, not by omission. Anything needing it inherits both prerequisites: the CONNECT-proxy containment **and** inbound DKIM verification.
- A scheme allowlist (`mailto:`/`https:` only) is required at both parse and render. Rendering an unvalidated URI reproduces CVE-2025-68673 exactly.
- The unsubscribe surface groups by **sender**; the cleanup pass groups by **category** (`email_cleanup.job_summary` → `GROUP BY category`). These are different axes and should not be flattened into one list.
- Bulk `mailto:` unsubscribe sends real mail from the user's address. Whether it counts as the hard-limited `mass_communication` action type is an open product decision recorded in the exploration.

## Revisit conditions

- **Inbound DKIM/ARC verification ships.** That removes the forged-header objection and makes the RFC 8058 gate real rather than decorative. It is the single largest unblocker.
- **The LUM-625 IMAP egress proxy is extended to a general outbound HTTP role** with an ACL denying RFC1918/loopback/link-local/metadata ranges. That removes the network-SSRF objection and the ceiling incompatibility.
- **The coverage census shows `mailto:` is rare** in real household mail — if the browser hand-off carries nearly all the value, the UI should be designed around it, and if *neither* path covers enough, sender suppression becomes the primary feature rather than the backstop.
- **`tethered` or the ceiling design gains a mechanism for scoped runtime host addition** that is auditable and does not require a restart. That would change the architectural analysis directly.

## Status history

- 2026-07-25: Draft created by `/explore` (LUM-697)
- 2026-07-25: Finalised at merge to `dev` — implemented in LUM-697
