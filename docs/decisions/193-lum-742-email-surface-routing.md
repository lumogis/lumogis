# ADR 193 — Information architecture for the email attachment and subscription surfaces

**Status:** Accepted
**Created:** 2026-07-29
**Last updated:** 2026-07-29
**Linear:** LUM-742
**Supersedes/relates:** ADR 191 (unsubscribe), ADR 192 (paperless mailbox overlap)

## Context

Five components under `clients/lumogis-web/src/features/email/` shipped across LUM-702, LUM-727, LUM-728 and LUM-697 with **no production importer** — each imported only by its own test. Every one of those tests passed, because they render the component directly. That is the gap render tests cannot see by construction: a component can be perfectly renderable and perfectly unreachable at once.

One of the five was `AttachmentIngestToggle`, the sole writer of `sources.attachment_ingest_enabled`. That column is `NOT NULL DEFAULT FALSE` (migration 067) and gates the ingest sweep, so the attachment feature was **inert in production** while six issues were closed against it.

A second, quieter defect made the first unfixable on its own: `attachment_ingest_enabled` was written by `PATCH /attachment-ingest` and **returned by no GET anywhere**, while the toggle takes `enabled` as a required prop. No correct consumer could exist — a parent had to guess `false` and would render "off" for a mailbox that was on.

## Decision

Place the five surfaces **by kind, not by convenience** — a split the components' own prop contracts already encode. `AttachmentReviewList` and `FiledFromEmailList` take an optional `sourceId` (cross-mailbox); `AttachmentIngestToggle`, `AttachmentRuleList` and `SubscriptionsView` require it (per-mailbox).

* **`/email/attachments`** — a route nested under the existing `/email` nav entry, hosting the review list with the filed list as a section. Reached from a two-item tab strip; `pathToNavKey` already matches `startsWith("/email")`, so no sixth top-level nav item is needed and `/email` itself gains only the strip.
* **Mailbox card** (`EmailSignalsView`) — a "File attachments from this mailbox" disclosure holding the toggle and the rules list; `SubscriptionsView` joins the existing "Clean up this mailbox" disclosure, per LUM-697's stated intent.
* **The zero state is the activation surface.** The toggle defaults off and never self-enables, deliberately, so routing the list alone would show every existing user an empty page with nothing to do about it. The empty state carries the per-mailbox toggles inline and states which of the three watermark outcomes applies before the user commits.
* **`GET /sources` exposes `attachment_ingest_enabled` and `attachment_ingest_since`** — the prerequisite for any truthful toggle mount.

### Selection: per-source quotas, not a cross-source merge

A cross-mailbox list must still offer "Always file from this sender", because that rule is the exit from the list — and a rule belongs to exactly one mailbox. So candidates carry `source_id` / `source_name` and the group key becomes **(sender, source)**.

Paging uses **per-source quotas** (`max(1, limit // n)`, remainder distributed, each source trimmed to its share). The obvious alternative — fetch from every mailbox, merge newest-first, take `limit` — does **not** fair-share: a mailbox whose mail is simply more recent takes every slot. It appears to preserve a newest-first ordering contract, but no such contract exists at this layer: the route sorts groups by `(-count, from_display)`, so recency only decides which candidates enter the window, never how they are presented. With nothing to trade, quotas are simply correct.

`n` is the count of the **caller's** eligible mailboxes; `_opted_in_sources` gained a `user_id` predicate, because its length becoming a denominator would otherwise have sized one household's page by another's mailbox count.

`candidates_for_user` keeps its exact signature — `_candidate_index` calls it on the accept/decline path — and paging lives in a sibling, `candidates_page_for_user`.

## Alternatives Considered

* **Mount all five into mailbox settings** — cheapest, and it amplifies the defect it would close: `EmailCleanupPass` is already a task buried in a settings disclosure, so this would scale that mistake to five.
* **A fourth lane on `/email`** — lanes are triage states of *signals*; an attachment awaiting a filing decision is not one, and adding it breaks the LUM-643 spine.
* **A snoozed-style disclosure** — precedented and cheap, but the wrong affordance for a counted queue, and it grows a 1023-line component.
* **A cross-mailbox subscriptions hub** (the Gmail/Outlook pattern) — the right long-term destination, but no cross-source subscriptions endpoint exists. Deferred rather than allowed to block this chunk.

Full detail: `.cursor/explorations/LUM-742-email-surface-ia.md`.

## Consequences

**Easier**

* Six previously-dormant issues (LUM-702, 721, 722, 727, 728, 729) become deliverable, and LUM-724's email branch executes outside tests for the first time.
* LUM-730, 731 and 732 gain a surface to grow into; LUM-734 and 738 can target real routes.
* An opt-in feature that nothing could enable becomes discoverable through its own empty state.

**Harder**

* `/email/*` is now a route family, so a future `/today` surface (LUM-666) generalises `/email` rather than replacing it.
* `AttachmentIngestToggle` has two mounts, so a contract change touches two call sites.
* The candidates DTO gained fields; run `npm run codegen` before chasing a type error that looks like a real bug.

**Future chunks must know**

* **The consent scope is now enforced server-side (LUM-743, 2026-07-29).** As shipped, this ADR noted that the server skipped its overlap probe whenever `acknowledge_paperless_overlap` was true for *any* source, making the client the only thing binding an acknowledgement to a mailbox — and the web test its sole enforcement. That is resolved: the request carries `acknowledged_source_id`, and a claim that does not match the rule's `source_id` counts as no claim, so the gate runs. Fails closed in every ambiguous case. The web test remains valuable as a UI regression guard, but the invariant no longer depends on client discipline.
* The badge count reads `pending_total`, **never** `total` — the latter is the page length after `rows[:limit]` and can never exceed the limit.
* Attachment activation nests **inside** email activation (`isEmailActivated`), so attachments inherit whatever that gate reads. **LUM-655 is decided ([ADR-205](205-lum-655-email-core-vs-plugin.md), 2026-08-06):** email is a core, opt-in integration activated by configuring a mailbox — not a marketplace plugin. `isEmailActivated` is the stable seam, so a future `email_enabled` toggle or marketplace registry changes the signal source without touching this nesting.
* Group identity is (sender, source). LUM-730 and LUM-731 must key on it.

## Revisit conditions

* **A cross-source subscriptions endpoint exists** → move `SubscriptionsView` to a hub.
* **`/documents` gains an origin filter** → reconsider whether the filed list still earns a section.
* **LUM-666 proceeds to a `/today` dashboard** → revisit whether attachment review is email-scoped or cross-signal.
* **A household routinely runs more than ~3 mailboxes** → revisit the inline activation panel (it stops scaling) and batch the paperless advisory, which currently fetches the same instance config once per mailbox.
* ~~**The ack is bound server-side** (carrying the `source_id` it answers) → the load-bearing web test stops being the sole enforcement.~~ **Met 2026-07-29 by LUM-743**, before this ADR was a day old.

## Status history

- 2026-07-29 (LUM-743): Consequence amended — the acknowledgement is now scoped server-side, so the "client is the only binding" caveat this ADR shipped with no longer holds.
- 2026-07-29: Accepted on verification of LUM-742. Draft was `.cursor/adrs/LUM-742-email-surface-ia.md`; amended twice before acceptance — once when the owner chose the server-side DTO change over a client-only fix, and once when critique round 3 found that the count source (`total`) was impossible and the "fair-shared truncation" wording had been implemented as a merge that does not fair-share.
