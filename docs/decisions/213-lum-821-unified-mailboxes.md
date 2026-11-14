# ADR-213: Unified read across mailboxes, per-mailbox configuration and send

**Status:** Accepted
**Created:** 2026-08-14
**Last updated:** 2026-08-14
**Decided by:** `/explore LUM-821`, finalised by `/verify-plan`
**Exploration:** `.cursor/explorations/LUM-821-unified-mailboxes.md`
**Linear:** LUM-821 (relates LUM-745, LUM-666, LUM-654, LUM-677, LUM-719, LUM-819)

## Context

A user can connect more than one mailbox — personal and shared, or personal and
work. LUM-821 asked the framing question the email arc had answered implicitly
and never explicitly: **is Lumogis a per-mailbox client, or an account-wide
assistant?**

The ticket assumed every email surface was scoped to one mailbox. It is not.
The reading surfaces — the triage lanes, the attachments review, search, and the
needs-you count that drives the morning brief — have always spanned every IMAP
source the user can see. The per-mailbox surfaces are all *mailbox
configuration*: subscriptions, the cleanup pass, organize settings, attachment
ingest rules, and the signature.

Four separate tickets arrived at that split independently, and one of them wrote
it down in a source comment rather than a decision:
`EmailAttachmentsPage.tsx` — *"their authors made the cross-mailbox surfaces
cross-mailbox and the per-mailbox ones per-mailbox."* This ADR promotes that
from a comment to a decision, because the next surface built should not have to
re-derive it.

## Decision

**Lumogis is an account-wide assistant. Email surfaces that READ are unified
across every mailbox the user can see; surfaces that CONFIGURE a mailbox, and
every outbound send, resolve to exactly one mailbox.**

A reply's mailbox is inherited from the message it replies to. A composed
message must name its mailbox explicitly — the send handler refuses anything
else. A unified reading surface must show which mailbox each message came from.

## Alternatives Considered

- **Unified by default including settings** — a signature, an organize rule and
  an unsubscribe each act on one server with one set of credentials, so
  unifying them means adding an "apply to which mailbox?" question to every one
  of them. More UI than the per-mailbox card it would replace, and it puts an
  aggregated surface in front of operations whose blast radius is not.
- **Per-mailbox with a switcher** — a regression from shipped behaviour, and it
  would reintroduce the count-vs-view mismatch LUM-821 set out to prevent:
  `count_needs_you_email` spans every IMAP source, so narrowing the view would
  make the morning brief describe mail the page cannot show.
- **A synthetic "unified mailbox" `source` row** — every ownership gate
  (`_load_owned_source`, `_require_owned`, credential resolution keyed on
  `instance=source_id`) would need a special case for a source that cannot hold
  credentials. A trap laid for the send path.

See the exploration for the full comparison and the prior-art survey.

## Consequences

**Easier**
- The next email surface has a rule to follow instead of a judgement to make:
  does it read, or does it configure?
- LUM-745 (cross-mailbox subscriptions hub) becomes an instance of a stated
  rule rather than a one-off, and LUM-666's "new `/today` vs generalise
  `/email`" fork narrows — `/email` is already a cross-source aggregate.
- The morning brief and the lanes are guaranteed to describe the same set of
  mail, because both omit `source_id` rather than agreeing by coincidence.

**Harder / foreclosed**
- Any "pick a mailbox first" landing page for `/email` is now off the table.
- ~~**A unified view owes the user a mailbox indicator, and Lumogis does not
  have one.**~~ **Shipped 2026-08-14 with this ADR.** `source_label` was already
  selected server-side, returned by the signals route and typed in the web
  client — and rendered nowhere. It now appears on every card as a quiet chip,
  and the rule is narrower than "show the mailbox": it is shown **only** when the
  viewer owns two or more mailboxes (with one, the answer is never in doubt and
  the label is a word repeated on every card) and **only** for a mailbox the
  viewer owns (a household member's shared mail offers no reply anyway, so naming
  their mailbox would disclose their setup to answer a question the card does not
  pose). `EmailSignalCard` renders what it is handed and decides nothing, because
  the decision depends on the source list rather than on any one row.

  Two things surfaced in building it that the decision did not anticipate. The
  accessible prefix is **real visually-hidden text, not `aria-label`** — a
  `<span>` with no role is `role=generic`, which prohibits naming, so a browser
  drops the label while the source and any attribute-querying test still look
  correct. And the chip is the first use of muted foreground on the alt surface
  (5.10:1 light, 6.04:1 dark), now asserted in `tests/design/contrast.test.ts`.
- ~~**Composing without an originating message needs a From rule.**~~
  **Settled 2026-08-15 (LUM-830), which is what this ADR's revisit condition
  asked for.** Compose-new shipped, and with it the first surface in the product
  that has to choose a mailbox rather than inherit one. The rule:

  * The **From row renders in compose mode always** — a picker with two or more
    sendable mailboxes, the mailbox named plainly with one. ADR-213 asked for
    the choice to be *visible*, not merely correct, and a row that appears only
    sometimes teaches nobody where the answer lives.
  * **Only mailboxes that can actually send are offered** — an own IMAP source
    holding an SMTP credential (`hasSmtpCredential`). Offering one with sending
    off would reproduce LUM-719, an affordance that fails at the send.
  * **Mailboxes but none enabled gets an explanation, not a missing button.**
    Sending is one settings toggle away, so silence is a dead end. Nothing is
    claimed while the credentials query is loading or failed — asserting "off"
    for a state we could not read is the mistake `deriveSendState` avoids.
  * The confirm card **names the chosen mailbox** rather than falling through
    to "your address".

  Two things surfaced that the decision had not anticipated. `SourceRow` carries
  **no email address**, so the From row shows the mailbox *name* — the same
  string the settings page and the lane chips use; showing the sending address
  would need a backend change. And the LUM-817 signature effect now re-runs when
  the mailbox changes, so it had to learn to **swap** the signature rather than
  append a second one — substituting the previous block only while it is present
  verbatim, which leaves an edited signature alone.

**What future chunks must know**
- Reading surfaces omit `source_id`. Configuration surfaces are mounted per
  mailbox card. Sends resolve one owned `source_id`, always.
- Aggregating across `source_id` adds no household-visibility risk: it is the
  absence of an extra filter on a query already scoped by `visible_filter`
  (lists) and `authored_by_filter` (bodies, search hits).

## Revisit conditions

- **A household reports losing track of which mailbox they are reading** even
  with the indicator shipped — then add the mailbox filter this ADR defers.
- **A mailbox is added whose mail should not appear in the unified lanes** (a
  shared or role account someone wants quarantined) — the decision assumes every
  connected mailbox is one the user wants in one queue.
- **More than ~3 mailboxes becomes a normal shape.** The recommendation is sized
  for the two-mailbox household; at five, a filter stops being chrome.
- ~~**Compose-new ships.** Revisit the From rule as part of that work, not before.~~ **Discharged 2026-08-15 (LUM-830)** — see § Consequences.
- ~~**A sending address becomes available on `SourceRow`.**~~ **Resolved
  2026-08-15 by LUM-831 — but not on `SourceRow`.** The instinct was right (an
  address is what a user recognises, and `sources.name` has no unique
  constraint, so the label could name two mailboxes indistinguishably) and the
  location was wrong. `GET /sources` is a broad, household-adjacent read; the
  sending address is an owner-only account identifier. It is served instead by a
  narrow owner-scoped endpoint that resolves it on demand from the credential —
  no column, no second copy, and it cannot drift from the address the send uses.
  See [ADR-214](214-lum-831-sending-address.md).

## Status history

- 2026-08-15: Revisit condition *"a sending address becomes available on `SourceRow`"* resolved by **LUM-831** — the address ships, deliberately **not** on `SourceRow`. Reason recorded inline above.
- 2026-08-14: Draft created by `/explore` (LUM-821)
- 2026-08-15: Amended (LUM-830) — the From rule settled when compose-new shipped.
- 2026-08-14: Finalised by `/verify-plan` — implementation confirmed the decision. The reading surfaces already implemented it; the mailbox indicator this ADR made a consequence shipped in the same pass.
