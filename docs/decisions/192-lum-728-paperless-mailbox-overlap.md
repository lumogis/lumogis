# ADR: Detecting a mailbox paperless-ngx also watches — propose, never auto-set
**Status:** Accepted (implemented)
**Created:** 2026-07-28
**Last updated:** 2026-07-28
**Decided by:** Opus 5
**Exploration:** `.cursor/explorations/lum-728-paperless-mailbox-overlap.md` (2026-07-27, + addendum 2026-07-28)

## Context

Paperless-ngx can monitor an IMAP mailbox itself. Lumogis reads the same mailbox
and, since LUM-727, offers its attachments for per-attachment filing. Point both
at one mailbox and the same invoice files twice: the two paths have disjoint
dedupe keys and no content-level key between them.

The obvious response — read paperless's `/api/mail_rules/`, work out which
Lumogis mailbox it refers to, and mark that mailbox "paperless-owned" — runs
into a matching problem that decides the whole feature.

`sources.url` is `imap://{host}:{port}/{mailbox_label}`, where `mailbox_label`
is a **non-PII nickname** (`personal`, `work`) that exists only to keep the URL
unique per host. It is not the username and not a folder
(`models/email_setup.py`, `routes/signals.py:82-88`). Two Gmail mailboxes in one
household therefore share host, port and TLS, and are separated **only by a
username inside the encrypted credential payload** — one decrypt per mailbox to
read. Neither system canonicalises the hostname it was typed
(`imap.gmail.com` / `imap.googlemail.com` / a bare IP / a Docker service name are
all one server and no two are string-equal).

So the cheap matcher — the one that stops at hostname and never touches secrets —
marks **both** mailboxes paperless-owned and silently stops filing on the one
paperless never touched. No error, no row, no log.

Two things then shifted under the feature. **Migration 069** (Message-ID
fallback) made `MOVE` recoverable, leaving **one** destructive `MailRule.action`
of five rather than two. And **LUM-729 shipped** a consent gate for the inverse
harm, which turns out to have built the precondition this feature needs and
decided two questions this one had left open.

## Decision

**Detection proposes. It never sets policy. In all five `MailRule.action` cases,
including `DELETE`.**

Findings carry a confidence computed from what was actually compared — `high`
(casefolded username **and** host equal, folder compatible) or `low` (host only).
A `low` finding is never presented as a recommendation, only as "paperless may
also be watching this mailbox", with the manual control beside it. `DELETE` earns
a stronger *presentation* — a pre-focused one-click "let paperless own this
mailbox" — not a stronger default. **The click is the write.**

The reason is that the two facts are independent: `action` tells you how bad an
overlap is *if the match is right*, and says nothing about *whether the match is
right*. Only the second governs whether auto-setting is safe, and the match is
least reliable in exactly the configuration where auto-setting does most damage.

Three consequences follow, and the third is the scope decision:

1. **Which doors obey.** ⚠️ **SUPERSEDED by D1 — see the status history.** This
   consequence presupposes a flag that D1 then removed, so there is nothing for
   any door to obey and `_opted_in_sources()` must be left unmodified.
   Recorded rather than deleted, because the reasoning still explains *why* the
   unattended door is the one that would have mattered. Original text:
   The unattended rules tick obeys the flag.
   `accept_candidates` warns but never gates and never removes a candidate.
   `refile_row` does not obey. One line: *the flag governs what Lumogis does when
   nobody is watching, and nothing else.* One predicate in `_opted_in_sources()`
   (`email_attachment_ingest.py:157`).
2. **Implement it as a refinement of the shipped consent gate**, not as a new
   findings mechanism — a second `code` alongside `external_consumer_may_starve`,
   reusing `acknowledge_external_consumer`. LUM-729 already established
   machine-readable code + explicit acknowledgement + warn-then-allow.
3. **Do not add `sources.attachment_filing_owner` (migration 072) yet.**
   `attachment_ingest_enabled` (067, default `FALSE`) is already a per-mailbox
   off switch and already the fourth of four gates. A new column would record a
   *reason*, not add a *capability*. Build the finding and the warning; add the
   column only if the warning proves insufficient.

**Paperless must never be load-bearing.** With no paperless source configured
this is a complete no-op — already true and already tested, via
`external_consumer_guard.has_external_consumer_strict`.

## Product decisions — owner-accepted 2026-07-28

The four questions this ADR was blocked on are answered. Each is grounded in a
surface that already ships, not a proposed one.

**The through-line, and the tie-breaker for anything not covered below:** prefer
the interaction that happens *at the moment of the decision* over a state the
user has to discover later, and never let a change in paperless silently change
what Lumogis does — **in either direction**. That is the principle LUM-729
reached from the opposite side of the same problem.

### D1 — `sources.attachment_filing_owner` (migration 072) does NOT ship. Gate rule creation instead.

The shipped explanation under the mailbox checkbox already settles it:

> Nothing is filed automatically. Attachments appear in a list for you to pick
> from, and you can set up rules to file the recurring ones.

So "paperless owns this mailbox" is not a persistent mailbox state. It is a
question that arises at exactly one moment — creating a filing rule on a mailbox
paperless also watches — and that moment has a route:
`POST /attachment-rules` (`routes/api_v1/email_attachment_ingest.py:453`), the
structural twin of the organize-settings PATCH LUM-729 gates.

- A column explains, months later, *why* filing is off — but only to someone who
  goes looking in mailbox settings. The gate speaks when it is actionable.
- **A column would make the shipped checkbox lie.** Silently switching a mailbox
  off leaves the checkbox either ticked-and-wrong or unticked-by-something-that-
  is-not-the-user. In a self-hosted product whose premise is that you control
  your own machine, a setting that changes itself is the most trust-corrosive
  pattern available.
- The user may already have met this dialogue on organize settings. A second,
  differently-shaped mechanism for the same idea teaches them there is no pattern
  to learn.
- It is the reversible direction: 072 remains available if the gate proves
  insufficient. Shipping a column and later removing it is a migration plus a
  "what did these values mean" question.

**Accepted cost:** someone acknowledges, forgets, and keeps getting duplicates.
Visible, and purgeable since LUM-724 Phase 2.

### D2 — Un-set is manual only, and under D1 it is nearly free.

With no column there is nothing to un-set; the rule exists or it does not, and
deleting it is a shipped button (`DELETE /attachment-rules/{id}`). When the
**paperless** rule disappears, Lumogis does nothing — correct, because the
Lumogis rule was an explicit act and still expresses what the user asked for.

Automatic un-set is refused because it would make Lumogis silently **start**
filing due to a change in another system. That is the "a paperless edit at 3am
changes Lumogis behaviour" objection running backwards, and backwards is worse:
starting to file *creates* documents. Users forgive a system that did not do
something; they do not forgive one that did something they never asked for.

### D3 — A `low`-confidence finding IS shown, but never as a gate.

| Tier | What matched | Interaction |
| --- | --- | --- |
| `high` | casefolded username **and** host, folder compatible | The LUM-729 consent gate — `422`, machine-readable `code`, must acknowledge |
| `low` | host only | **No gate.** One non-blocking line in the rule-creation form |

Proposed `low` copy: *"You also run paperless, and it watches a mailbox on this
server. If it's this one, this rule may file the same document twice."*

- **Different error costs deserve different interaction costs.** A wrong `high`
  costs one click. A wrong `low` is *frequent* — the ordinary configuration that
  produces it is two mailboxes on one provider, common in a household. Gating on
  it trains users to dismiss the dialogue, and the dismissal **transfers** to the
  `high` one that mattered. Alarm fatigue does not degrade gracefully.
- **Suppressing `low` entirely abandons the household with a real overlap on a
  host-only match** — silently, which is the failure this whole ADR is organised
  against.
- **A non-blocking line can be honest about its own uncertainty.** *"If it's this
  one"* is a true sentence. A modal that says "maybe" is a bad modal: a modal
  implies the system knows something.

### D4 — The household non-gating gap is accepted here, and ticketed elsewhere.

Its shape is what makes it tolerable *only* if stated out loud: **the person who
suffers is not the person who acts.** The admin owns paperless; a member turns on
folder sorting for their own mailbox; the admin's archive develops holes. Neither
sees anything, and the member cannot know they caused it.

Accepted here because closing it means reading the admin's paperless credentials
to decide a member's mailbox policy — crossing the boundary every `SCOPE-EXEMPT`
comment in this codebase exists to police. The fix belongs in **LUM-626**
(instance-aware connector credentials), not bolted onto this ticket. The
single-user self-hoster — the common deployment — is fully covered today.

**Considered and deliberately left open:** surfacing it on the *admin's* side
instead ("3 mailboxes in this household sort mail into folders"), so the person
who owns paperless can see the risk without Lumogis making policy for anyone
else. It carries its own tension — it tells the admin something about members'
mailbox configuration, which in a household product is not automatically theirs
to know. That trade should be decided deliberately, not slipped in with this.

## Alternatives Considered

Full detail in the exploration; each is rejected on a Lumogis-specific ground.

- **Auto-set the owner for destructive actions** (the ticket's original
  recommendation) — makes a third-party system's configuration authoritative over
  Lumogis behaviour, contradicting the hard constraint that paperless never be
  load-bearing. A paperless rule edited at 3am would change what Lumogis does, and
  the reverse — rule deleted, flag stays — needs an un-set path nobody specified.
- **Match on hostname only** — the cheap option, and the one that produces the
  silent-absence failure. Rejected because absence is the failure no surface
  renders, whereas duplication is visible and, since LUM-724 Phase 2, purgeable.
- **Resolve hostnames and compare addresses** — explicitly refused on the
  enumeration path in this codebase; `declared_imap_hosts` documents why.
- **Blanket "paperless owns the mailbox"** — with `MOVE` recoverable since 069,
  this would disable Lumogis filing in **four of five** action configurations
  where nothing was broken.
- **Gate `accept_candidates` too** — the user named a specific file and pressed a
  button; silently dropping it from the list is the same invisible-absence failure
  in a surface where the user is actually present.
- **Do nothing** — defensible, and the honest baseline. Duplication is visible and
  purgeable. This ADR is only worth building because LUM-729's coarse warning
  now fires for users it does not apply to.

## Consequences

**Easier.** The no-paperless no-op, the fail-open/raising asymmetry, and the
consent-gate UX are all shipped and tested (`external_consumer_guard.py`,
26 unit tests). Detection starts *after* a predicate that already exists. Reusing
the gate means no new table, no new surface, and open question 4 ("does the
finding persist?") largely dissolves — the gate recomputes per request.

**Harder.** Reading `username` for a `high`-confidence match means a credential
decrypt per mailbox on a periodic path. That is the step the cheap
implementation skips and the reason the cheap implementation is wrong.

**A gap this ADR inherits rather than closes.** LUM-729 scoped the guard
per-user (`WHERE user_id = %s`), because a household member's paperless source
must not gate another member's mailbox. Paperless is admin-owned and
single-instance (`paperless_credentials.load_connection` has no `instance`);
mailboxes are per-user. So in a household, **members are not gated at all** —
their mailboxes can starve the admin's paperless and nothing warns anyone.
LUM-728 does not get to quietly cross that boundary. Whether the gap is
acceptable is a product call, and LUM-626 is where the plumbing changes.

**The feature's value is repriced, and this should survive into the plan.** The
shipped guard's own docstring says a false positive costs one click and a false
negative costs documents "at about one in three". LUM-729 took the false
negatives. What LUM-728 buys is **fewer spurious warnings** — a usability
improvement, not a data-loss fix. Medium priority is right.

**What this closes off.** Any design in which paperless configuration silently
changes Lumogis behaviour. Any matcher whose failure mode is absence rather than
duplication.

**Related tickets move.** LUM-725 drops from "the fix" to "a preference for where
documents land". LUM-282 is where the confidence tiers and degrade copy must
live. LUM-723 is rescoped from cross-mailbox to cross-path dedupe. LUM-626's
`instance` work is a prerequisite for the household case.

## Revisit conditions

- **`paperless_credentials.load_connection` gains an `instance` parameter**
  (LUM-626). The "the user's paperless" assumption in this decision is only true
  while paperless is single-instance.
- **paperless-ngx exposes the monitored folder set in a form that does not need a
  credential decrypt to match** — e.g. a mail-account identifier Lumogis already
  stores. That would remove the entire matching risk this ADR is built around.
- **`sources` gains a stored, non-PII mailbox identity** that distinguishes two
  mailboxes on one host without reading the encrypted payload. Same effect.
- **The refined warning proves insufficient in practice** — users keep filing
  duplicates despite it. Then `attachment_filing_owner` (migration **072**, not
  071 — 071 shipped with LUM-729) becomes justified, and the un-set question must
  be answered before it lands.
- **Lumogis ships a second external consumer** besides paperless. The guard's
  `source_type = 'paperless'` predicate and this ADR's whole framing become one
  case of a general problem.

## Status history
- 2026-07-28: Draft created by /explore (LUM-728), from the 2026-07-27 exploration plus its 2026-07-28 addendum
- 2026-07-28: **Four product decisions accepted by the owner** (D1–D4). `/create-plan` is unblocked. Stays **Draft** — finalises at merge, per the convention in this repo.
- 2026-07-28: **Finalised at `/verify-plan`.** Implemented as `docs/decisions/192-lum-728-paperless-mailbox-overlap.md`. Every decision below survived implementation and two external review passes unchanged; what changed was the *implementation*, not the *decision* — eight defects, all of which failed silently. Two consequences worth carrying forward: (a) `status: "ok"` genuinely cannot be trusted as proof of non-overlap, both because paperless permission-filters to an empty list and because a truncated page now reports `unavailable`; (b) the whole client half sits on components no route imports, so the gate is correct and currently unreachable — see LUM-727.
- 2026-07-28: **Revised during `/review-plan --arbitrate` R1** (critique: fable-5). Decision consequence **1 ("which doors obey") is marked superseded by D1** — it presupposed the `attachment_filing_owner` column that D1 removed, so the ADR and the plan were in permanent written disagreement. The plan had resolved this correctly in prose; the ADR now says so too, which matters because **LUM-282 and LUM-626 both read these decisions next**. No decision changed — this is a consistency fix, not a reversal.
- 2026-08-16: **Consequence (b) of the finalisation entry above is stale and is corrected here, not deleted.** It recorded that "the whole client half sits on components no route imports, so the gate is correct and currently unreachable — see LUM-727." LUM-727 has since shipped: `App.tsx` routes `/email/attachments` to `EmailAttachmentsPage` → `AttachmentReviewList`, which is where **both** tiers now land — the `high` refusal via `paperlessOverlapWarning` on the `POST /attachment-rules` 422, and the `low` advisory via `fetchPaperlessOverlap` against the GET route that exists precisely because a 422 cannot carry a non-gating finding. Rule creation lives only on that surface (`AttachmentRuleList` is view-and-delete), so there is one gate point rather than a second ungated door. Consequence (a) — that `status: "ok"` is not proof of non-overlap — still holds. Left in place rather than rewritten because the *reason* the gate was unreachable is what made LUM-727 a prerequisite, and a reader who finds only the corrected state cannot see that the two tickets were coupled. **The next reader of this ADR is still LUM-282 or LUM-626; neither should be planned against the "unreachable" claim.**
- 2026-08-16: **"Related tickets move" said LUM-723 was rescoped from cross-mailbox to cross-path dedupe; the ticket never was.** Its title and body still described the per-mailbox key only, so the structural remedy for the duplication this ADR warns about was both unbuilt and untracked under the description that would let anyone find it. LUM-723 is rescoped now, and the audit that found the drift confirms the gap is real: nothing compares `external_documents.content_hash` against `email_attachment_ingest_log.content_sha256`. The consent gate remains a *warning*, not a dedupe — which is what this ADR always said it was, and is why the priced value here is "fewer spurious warnings", not "no duplicates".
- 2026-08-16: **Extended by LUM-744/835/836 — the decision holds, and one of its consequences turned out to have been ignored rather than applied.** Consequence 2 says *"implement it as a refinement of the shipped consent gate, not as a new findings mechanism — a second `code` alongside `external_consumer_may_starve`, reusing `acknowledge_external_consumer`."* The shipped gate honoured the first half at `POST /attachment-rules` and nobody applied it at `POST /sources`, where the **connect-time** ordering lives. That gap is now closed: a second arm sits beside `ARM_CONNECT_PAPERLESS`, carrying `OVERLAP_CODE` — the second code this consequence asked for, and the one `paperlessOverlapWarning.ts` already matched. **One deliberate divergence from the letter of consequence 2:** it says "reusing `acknowledge_external_consumer`", and the implementation adds a *separate* `acknowledge_paperless_overlap` instead. Reusing the one boolean would mean a user who accepted the starvation dialogue had silently accepted the duplication one — two opposite harms answered by one click, with the second never shown. The field name is taken verbatim from `create_attachment_rule` (LUM-743), so the same consent is called the same thing on both routes. LUM-743's companion `acknowledged_source_id` is deliberately not carried: it exists because a rule names one mailbox and a consent for A must not create a rule on B, whereas connecting paperless is a single act against all the caller's mailboxes, so there is no second subject to misapply it to. **D1 (nothing persisted) is intact** — every new surface recomputes on view and stores nothing. **D3 (`low` never gates) is intact** — the connect arm filters to `high` before refusing, and the rules-page line is not a gate. **D4 (household non-gating) is unchanged, and now stated rather than implied**: `POST /sources` is `require_admin` and reports only the caller's own mailboxes, so in a household a member owning the filing rules is still not warned. **The revisit condition about `status: "ok"` not being proof of non-overlap is now load-bearing in two more places** — both new surfaces render on a positive finding only and stay silent on `auth`/`unavailable`, each pinned by a test.
- 2026-11-06: **Amended by LUM-844 ([ADR 226](226-lum-844-source-pause.md)) — a paused paperless source still counts as an external consumer.** `has_external_consumer_strict` dropped its `AND active = TRUE` predicate. paperless-ngx runs its own mail rules whether or not Lumogis polls its REST API, so pausing the source in Lumogis does not stop it reading the mailbox; the Do-mode `imap_move` force-Ask and the organize / clean-up warnings keep firing for a paused source. This reverses the intent recorded in the guard's original test ("a source they disabled must not gate them at all"), which was written while `active` could never be false. A user who no longer uses paperless deletes the source. The IMAP folder-sort query (`has_folder_sorting_mailbox`) keeps `active = TRUE`.
