# ADR-198: Where the send capability lives on a mailbox card

**Status:** Accepted
**Draft history:** `.cursor/adrs/LUM-769-send-capability-surface.md`
**Created:** 2026-08-01
**Last updated:** 2026-08-02 (accepted on verification of LUM-769)
**Decided by:** `/explore LUM-769` (claude-opus-5)
**Linear:** [LUM-769](https://linear.app/lumogis/issue/LUM-769/no-ui-can-enable-send-on-an-existing-mailbox-the-opt-in-is-a-one-shot)
**Relates:** ADR 197 (twice amended by LUM-771/772, whose contract this consumes), ADR 197 (LUM-759 SMTP opt-in error contract), ADR 195 (LUM-633 poll health), LUM-650 (the opt-in itself)

## Context

"Let Lumogis send as me" can be chosen once, during mailbox setup, and never afterwards — `setEmailSmtp` has a single caller inside the create form. The capability is complete and tested server-side; LUM-759 fixed its error contract and wrote all eight failure strings. Only the control is missing.

The ticket asks for a toggle on the mailbox card. Reading the surface changes the question.

## Decision

Six things are settled. Three hold under every candidate design; the fourth and fifth were decided on verification, each by a constraint rather than by taste; the sixth was found while planning and changes the interaction.

**1. Send *state* needs no backend change. Do not add an `smtp_enabled` column.**

> **⚠️ Revised after critique round 1.** This decision was originally titled *"This is a web-only chunk"*, and that headline was **false** — see Decision 6. The narrow claim below survives; the sweeping one did not, and it was the plan's cost estimate.

SMTP enablement is not state on `sources`; it is the existence of a credential row, and `GET /api/v1/me/connector-credentials` already publishes `{connector, instance}` per row under an `extra="forbid"`, never-plaintext projection. `items.some(i => i.connector === "smtp" && i.instance === src.id)` is the answer. `email_credentials.list_mailboxes`'s docstring states this is what the surface is for.

Adding an `smtp_enabled` **column** to `sources` is still rejected: a second source of truth for a question the credential row already answers, plus a migration, plus a drift class where the two disagree. (Decision 7 adds a *computed*, non-persisted field to the same response — which is not the same thing, and the distinction is the point.)

**2. It is not a toggle in the NN/g sense, and must not be built as one.**

The control is asymmetric. Off→on is one click for an app-password mailbox reusing its IMAP password *and* for an OAuth mailbox (`reuse_imap` mints from the already-verified token) — but needs a password field for a separate sending credential, and `smtp_host`/`smtp_port` for an "Other" provider. On→off is always instant.

A switch that opens a form when flicked is the specific anti-pattern NN/g names. The shape is **progressive disclosure**: a switch for the two default paths, fields revealed only for the two that need them. Both defaults are the common case, so a design that imposes a form on everyone serves the minority.

**3. Sending is not a peer of the other mailbox capabilities, and the surface must not imply it is.**

Organize, clean-up and attachment-filing are reversible operations on data Lumogis already holds — two of them ship explicit undo. Sending is an irreversible outbound action in the user's name, to third parties, with no undo. LUM-650 encoded this server-side: a separate credential row, independently revocable, never auto-enabled on the password path.

The mailbox card currently renders three `<details>` panels, two buttons, a mode label, a health banner and a remove button inside one `<li>`. A fourth `<details>` would place the only irreversible capability behind the pattern NN/g warns about at exactly this density — and the card already cannot answer "what is Lumogis doing with this mailbox?" without three clicks.

**4. The surface is an always-visible line for each capability whose state is already readable — today that is Send and attachment-filing, and it is not a four-row strip.**

*Decided 2026-08-01. This ADR first left the choice open between a four-row capability strip and a send-only line, expecting the owner to pick on scope. Checking that each row could state a **true** value settled it on technical grounds instead:*

| Row | State from | Clean? |
|---|---|---|
| Read | `GET /sources` → `extraction_method` / `full_ingest_active` | ✅ already fetched |
| File attachments | `GET /sources` → `attachment_ingest_enabled` | ✅ already fetched, currently **discarded** |
| Send | `GET /me/connector-credentials` → a `connector=="smtp"` row | ✅ one new query |
| **Organize** | a **separate taxonomy query** (`EmailOrganizeSettings.tsx:42`), and "on" is category-shaped, not boolean | ❌ |

Organize would need a third query **per mailbox** — N+1 across a household — to fill a row that still would not be a clean on/off. **A strip with one guessed row is worse than no strip:** uniform-looking rows that are not uniformly trustworthy is the LUM-748 failure mode, a surface implying rigour it does not have.

So: promote the two states that are already free, leave Organize and Clean-up as they are, and let the strip become available for free if Organize ever gains a per-mailbox flag.

**5. Turning sending off confirms once, with copy that names the risk rather than detecting it.**

The intended design was to confirm **only** when destructive — when a separately-entered SMTP password would be lost. That is not buildable: `ConnectorCredentialPublic` is metadata-only and carries no payload, so the client cannot see `reuse_imap`. Widening a wire shape that is `extra="forbid"` precisely to stay minimal, in order to decide a dialog, is the wrong trade. The copy absorbs the uncertainty:

> *Turn off sending? If you set a separate sending password for this mailbox, you'll need to enter it again to turn sending back on.*

True in both cases, requires no schema change, and does not pretend to knowledge the client lacks.

**6. The route answers in FIVE shapes, and `set_email_smtp` cannot enable an OAuth mailbox at all until it is fixed.**

> **⚠️ Revised after critique round 1, which both critics reached independently.** This decision originally said "three outcomes, not two". Two more exist, and one of them is a defect that makes Decision 1's "web-only" headline false.

**The defect.** `set_email_smtp` computes `password = imap.password` on the reuse path and calls `verify_smtp_connection` with **no `auth_type`, no `access_token`** (`signals.py:616, :624-630`). For an xoauth2 mailbox `_connection_from_payload` sets `password = None` — *"the connection itself carries no password"* (`email_credentials.py:438-439`). So the verify attempts app-password auth with an **empty string** and fails deterministically. `verify_smtp_connection` has supported xoauth2 since LUM-693; the device-flow route passes the arguments ten lines away (`:780-788`). Because that flow also **auto-enables** SMTP (`:819`), disable currently works for an OAuth mailbox and enable cannot — strictly worse than the defect this ticket exists to remove.

**The five shapes**, discriminating on `ok`, then `enabled`, then `tested`:

| Response | Meaning |
|---|---|
| HTTP 4xx/5xx with a `code` | credential could not be resolved (LUM-759's eight codes) |
| `200 {ok:false, enabled:false, tested:true, error}` | connect-test ran and failed; nothing persisted |
| `200 {ok:true, enabled:true, tested:true}` | enabled and verified |
| `200 {ok:true, enabled:true, tested:false}` | egress-blocked **soft-save** — saved, never verified |
| `200 {ok:true, enabled:false, tested:false}` | a successful **disable** (`:551`) |

The last two are byte-identical except for `enabled`, so a table keyed on `{ok, tested}` renders a successful disable as *"on — not yet verified"*. The soft-save is the other trap: a success response for a send path never proven to work.

**And the `smtp_auth` reveal must not fire for an OAuth mailbox.** `200 {ok:false, error:"smtp_auth"}` normally means *"the mailbox password is not accepted for sending"*, which is exactly when a separate sending password is worth asking for — the reveal-by-evidence that makes the common case form-free. But an Outlook tenant with SMTP AUTH disabled produces the same code for an OAuth mailbox, where **no password exists to give**. The server knows `is_oauth`; it must say so distinguishably, and the client must not reveal a password field on that branch. Otherwise the chunk re-ships, on the minority path, the trap Decision 0 exists to remove.

**7. The server publishes whether a mailbox needs a submission host. The client never guesses, and never spends a request finding out.**

*Added after critique round 2, replacing a react-don't-predict design that was itself a correction of a predict design.*

An "Other" mailbox — no registry entry — cannot be enabled without `smtp_host`/`smtp_port`, and the route answers `422 smtp_host_required`. Two designs were tried and both were wrong:

* **Predict client-side** (first draft) — re-implements `provider_for_host` in the client. Two matchers over one question, free to drift: the defect class ADR 195's "classifiers must agree" rule exists to prevent.
* **React to the 422** (self-review reversal) — no duplication, but for the population it affects it is not "one wasted request on a rare path". It is a **guaranteed** failure, then fields, then a *likely* second failure (`smtp_auth`, since non-registry mailboxes commonly have different IMAP and SMTP credentials), then a third submit.

**`GET /sources` gains `smtp_host_required: bool`, computed per row with the same `provider_for_host(host)` call the route already makes.** Not stored, so no migration and no drift — it is derived from `sources.url` on every read, by the one function that owns the question. The client reveals host/port up front for exactly those mailboxes and never issues a request it knows will fail.

This is the answer both earlier designs were groping at: **the server owns the matching *and* says the answer out loud.** It is also why Decision 1's rejection of an `smtp_enabled` *column* still stands — a computed field on a response is not a second source of truth; a persisted column would be.

## Alternatives Considered

* **A four-row capability strip** (Read / Organize / File / Send) — the original recommendation, **withdrawn on verification**: Organize has no per-mailbox boolean, so one row of four could not state a true value. Not wrong in principle; unbuildable honestly today. Revisit if Organize gains a flag.
* **A fourth `<details>` on the mailbox card** — the path of least resistance. Rejected in the exploration: it buries the highest-stakes capability on a card that already hides three panels, and is the option a future exploration reverses.
* **A separate top-level "Sending" section** (Thunderbird's Identity/Account split) — the strongest external precedent and the wrong scale. Thunderbird earns the split because identities are many-to-one with accounts; Lumogis pins one identity per mailbox (`From` is always the authenticated account, LUM-705), so a separate section lists the same mailboxes twice. Revisit if per-mailbox aliases ship.
* **A modal** — a dialog for something that is one click in the majority case, and it discards the state-at-a-glance property that is most of the value.
* **Reusing `AttachmentIngestToggle` verbatim** — a pure boolean over a `sources` column, no credential input, no failure vocabulary. Copying it would hide the asymmetry and the eight failure codes.
* **Auto-enabling send on the app-password path** to match OAuth — LUM-650 made it opt-in deliberately. Consent is the feature, not friction.

Full detail: `.cursor/explorations/LUM-769-send-capability-surface.md`.

## Consequences

**Easier**

* A user can turn sending on or off for a mailbox they connected earlier, without removing and re-adding it (today's only route, via LUM-701).
* **LUM-759's OAuth branches become reachable for the first time.** An OAuth mailbox never hits `PUT …/smtp` today, so `503 mailbox_unreachable` / `503 oauth_unavailable` / `422 imap_reauth_required` are correct, tested, mutation-checked — and unhittable in a browser. This chunk is what makes them user-testable.
* The failure copy already exists (`sendOptInMessage.ts`, eight codes, mirroring `pollHealthMessage.ts`). Whatever surface is chosen inherits it.

**Harder**

* **Sending state comes from a different endpoint than everything else on the card.** Mode, health and attachment-filing come from `GET /sources`; send comes from `GET /me/connector-credentials`. Two queries, two invalidation paths, and a window where they disagree after a mutation. That is the price of not adding a redundant column, and it is the right trade — but it must be handled deliberately, not discovered.
* If the capability-strip option is taken, a future capability is expected to declare a state string and a control. A small standing obligation, and the point of the design.

**Future chunks must know**

* **Do not re-invent the send failure copy.** `sendOptInMessage.ts` owns it, and a second vocabulary for one condition is exactly what LUM-760 exists to fix.
* **Do not add `smtp_enabled` to `sources`.** See Decision 1.
* **`delivery_paused` exists on the credential wire shape and SMTP does not use it.** It is a ready-made "configured but suspended" third state, currently set only by ntfy. Deliberately unused here — a state nobody sets is a branch nobody tests — and the natural home for a future "sending suspended after N consecutive failures".

## Revisit conditions

* **Per-mailbox send aliases ever ship** → Thunderbird's Identity/Account split starts earning its keep; revisit Option 4.
* **A fifth mailbox capability arrives** → if the card is still accordion-per-capability, that is the signal the strip was the right call and was deferred once too often.
* **Repeated send failures become a real complaint** → wire `delivery_paused` for SMTP and give the surface a third state.
* **`GET /sources` gains SMTP state for an unrelated reason** → the two-endpoint consequence above disappears; simplify.

## Status history

- 2026-08-01: **Revised again after critique round 2.** Two round-2 critiques led with the same P0: this ADR's Round-1 arbitration claimed *"ADR revised: yes"* and **the ADR was never touched** — it still asserted "web-only chunk, do not add a backend field" and "three outcomes, not two", both superseded by the arbitration being written at the time. That is the LUM-757 claimed-but-unapplied failure mode, reproduced in the log that described it. Decisions 1 and 6 are now actually revised, and **Decision 7 is new**: the server publishes `smtp_host_required` on `GET /sources`, replacing a react-don't-predict design that was itself a correction of a predict design. Both earlier attempts put the question in the wrong place; the server already owns the matcher and can simply say the answer.

- 2026-08-01: **Decision 6 added at `/create-plan`.** Reading `verify_smtp_connection` showed the route answers in three shapes, not two — the third being a *success* response for an opt-in that was saved but never verified, which a naive implementation renders as a plain "on". It also supplied a better interaction than the exploration proposed: the `smtp_auth` failure is exactly the moment to ask for a separate sending password, so the field is revealed by evidence and the common case never sees a form.

- 2026-08-01: **Surface choice decided, reversing this ADR's own recommendation before any planning.** It had recommended a four-row capability strip and left the pick to the owner as a scope call. Verifying that each row could state a true value showed Organize cannot — a separate taxonomy query, N+1 per mailbox, and not a boolean even then. One guessed row in a strip whose premise is uniformity is worse than no strip, so the answer is the honest subset: always-visible lines for Send and attachment-filing only. Decision 5 added for the same reason — the intended conditional confirm is not buildable, because the credential wire shape carries no payload.

- 2026-08-01: Draft created by `/explore` LUM-769. Three constraints decided; the surface choice left open. Impact scan found no 🔴/🟡 rework — LUM-760 is 🟡 on *sequencing* only, so no Linear block edges were registered.

## Finalisation — accepted on verification of LUM-769 (2026-08-02)

**Implementation confirmed the surface decision and falsified this ADR's own Decision 1 before it was ever written up.**

Decision 1 said: *"This is a web-only chunk. Do not add a backend field."* Both round-1 critics, blind to each other, proved the route **could not enable an OAuth mailbox at all** — `set_email_smtp` dropped `auth_type`/`access_token`, so an xoauth2 mailbox was connect-tested with an empty password, and because the device flow auto-enables SMTP, off worked and on could not. That is the finding that produced **LUM-771** and **LUM-772**, two server chunks and two amendments to ADR-197. Only after they shipped was this chunk genuinely web-only.

The surface decisions all held: a **state line plus a named action button** rather than a switch (the interaction is asymmetric); **no optimistic UI**; **one credentials request** for the whole list; **no `smtp_enabled` column** — send state is the existence of a credential row, and a persisted flag would be a second source of truth.

Three things implementation added that four review rounds did not predict:

* **`required` on an input is inert without a `<form>`.** The plan specified `required` as the mechanism that "catches the empty case before a request is made". With a plain button there is no submit event to gate, so every blank submission would have made a guaranteed-failing round-trip. Found by code review at implementation; the enable path is a real form, and mutation J pins it.
* **A live region per card is a breaking change to sibling tests.** Decision 9 puts the state line inside a `role="status"` container so a successful enable is announced rather than silently redrawn — which is correct, and which made `getByRole("status")` ambiguous in two pre-existing test files. Fixed by giving the three view-level messages identity, not by weakening the assertions.
* **The soft-save state cannot be derived from the query.** The plan specified it both ways; the control's own invalidation would have erased it one refetch later. It is owned by the view, and the only test that can see the seam is the one that crosses a mutation *and* its refetch.

**Consequence for future chunks:** the client still cannot tell an OAuth mailbox from an app-password one — `SourceRow` carries no auth method — so three behaviours here rest on server-supplied codes or on copy written to be true either way. That seam is unguardable by any client test, and publishing the auth method is filed against LUM-650.
