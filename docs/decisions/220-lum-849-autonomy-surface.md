# ADR-220: The autonomy surface — one page for "what may Lumogis do on my behalf"

**Status:** Finalised
**Created:** 2026-08-25
**Last updated:** 2026-08-25
**Decided by:** `/explore` (LUM-849), finalised by `/verify-plan`
**Finalised copy:** `docs/decisions/220-lum-849-autonomy-surface.md`

## Context

`/me/permissions` is the only place a person can say whether Lumogis acts on
their behalf without asking. It renders raw connector ids (`imap`, `smtp`) and
raw modes (`ASK`, `DO`) in a bare table with no sentence saying what is being
decided or what it covers.

LUM-834 chunk B tried to move that control onto every connector card and could
not: the nine connectors on `/me/connectors` cannot act, and the two that can
are hidden from that page by the same chunk. ADR-219 decision 8 recorded the
resolution — keep the page, fix its copy — and deferred one question: whether
connector Ask/Do modes and capability scope grants converge onto one page.

**That deferral rested on a false premise.** ADR-219 decision 8, LUM-849's body,
and the comment at `MeConnectorsView.tsx:120-126` all state that
`/me/tools-capabilities` already renders capability scope grants via
`GET /me/capability-grants`. Verified against source:

- There is no `/me/capability-grants`. The route is
  **`GET /api/v1/me/permissions/capabilities`** — a sub-path of the *permissions*
  router (`routes/connector_permissions.py:353`, router prefix at :68).
- `/me/tools-capabilities` calls `GET /api/v1/me/tools` and never touches the
  grants route (`MeToolsCapabilitiesView.tsx:48`).
- `list_my_capability_grants` has **no web consumer at all**.

And the gap is not "settable but never listed". A `capability.{id}` mode is
**enforced**: `execute_capability_http` → `permission.may_execute` →
`check_permission` → `get_connector_mode("capability.{id}")`
(`services/execution.py:280`, `:120-122`; `permissions.py:154-158`). With no row
the lazy default is `ASK`, and `may_execute` passes no `approved`, so **every
capability write tool is denied by default from a setting no surface lists.**
`/me/tools-capabilities` shows the consequence as `Permissions: blocked` — a
wall, where the truth is "it would ask you, if anything asked".

`CAPABILITY_SERVICE_URLS` is unset by default (`main.py:947-969`), so this is
latent today and goes live with the first configured capability service — which
is what LUM-622 and LUM-507 exist to make easy, both on `milestone:v1.3`.

## Decision

**Keep `/me/permissions` as the single autonomy surface. Rename its nav label to
say autonomy rather than access control, rewrite its rows as questions a person
can answer, cross-link to it from Email signals, and union its list across the
action *and* capability registries so the set of connectors it lists equals the
set `PUT` accepts and the set `check_permission` enforces.** Capability **scope
grants** appear on the same page as a read-only review, not as a second set of
dials: autonomy is an account-level mode, scopes are a consent artefact granted
at connect time. The grain stays **per connector** — never per mailbox, never
per instance, and not yet per action. Rows are built from the API response
rather than hard-coded, so a third mode (LUM-191) or a finer key (LUM-131) costs
a migration and not a page rewrite.

## Alternatives Considered

Full detail: `.cursor/explorations/LUM-849-autonomy_surface.md`.

- **Copy and rename only, leaving the capability gap recorded** — everything in
  it is right, but it ships the page still claiming to answer "what may Lumogis
  do" while omitting a live gate. Kept as chunk A, not as the whole.
- **Fold Ask/Do into Email signals and delete the page** — encodes "autonomy =
  email" into the IA and strands an already-enforced second occupant. Google
  puts third-party account access under Account → Security rather than under
  Gmail for exactly this reason.
- **Merge Permissions into Tools & capabilities** — the catalog is per-tool, the
  setting is per-connector; five `imap` rows would move together and read as
  five broken settings.
- **One row per action** — not persistable against
  `UNIQUE (user_id, connector)` (migration 016), and nine questions is how the
  one that mattered gets clicked through. Deferred to LUM-131.
- **Per-mailbox / per-instance grain** — no instance column, and it opens a hole
  per-connector does not: a mailbox added a year later acts under a default
  nobody chose. The per-mailbox dial already exists as the SMTP opt-in.
- **A seventeenth `/me/` page** — `MeNav.tsx` already carries 16; the problem is
  the name, not the absence of a page.

## Consequences

**Easier.** The page's claim becomes true: what it lists is what is enforced.
Every future surface that wants to answer "what may Lumogis do on my behalf" —
LUM-845's risk badges, LUM-462's cost/benefit UI, LUM-507's plugin consent —
links here instead of re-answering, so the answer has one voice. The
reversible/irreversible split gives email two honest questions instead of nine
toggles. Data-driven rows make LUM-191's third mode and LUM-131's finer grain
cheap.

**Harder / foreclosed.** Per-mailbox autonomy is closed on the record for the
third time; LUM-626 must not reopen it. Capability scope grants are a *review*
here, not a dial — consent belongs at install time, which constrains LUM-462 and
LUM-507's UI shape. The route path `/me/permissions` stays even after the label
changes, so the URL and the label will disagree until someone adds a redirect.

**What future chunks must know.**
1. The action registry **degrade-allows** on failure (`Warning: 199`) and the
   capability registry **fails closed**. A union must preserve *both*
   contracts — an action-registry outage must not hide capability rows, and a
   capability-registry outage must not invent them.
2. ~~`list_my_permissions` cannot attach the `Warning` header~~ — **fixed in
   this chunk.** It now takes an injected `Response` and attaches
   `Warning: 199` on the degrade branch, so "this list is incomplete" is
   sayable on the wire and the page renders it. The old comment claiming this
   was impractical is deleted. **Rows can still vanish under registry failure —
   that is the branch's nature — but they no longer vanish silently.**
3. ~~The web `PermRow` omits `scopes`~~ — **fixed in this chunk**, typed on the
   client but deliberately **not rendered**: giving it a display treatment with
   no capability service to test against would invent a contract. LUM-851 owns
   its display.
4. Under ASK a capability write is a **wall**, not a prompt — `may_execute`
   passes no `approved`, unlike the email path where a proposal satisfies ASK.
   Whether that asymmetry is deliberate is unresolved.


## What implementation confirmed, and the one thing it changed

**Confirmed.** The decision shipped as recorded: the page stayed at
`/me/permissions`, the nav label became "What Lumogis may do", the copy became
two questions grouped by reversibility, the grain stayed per-connector, and
scope grants stayed a separate layer (typed on the wire, deliberately unrendered
until LUM-851 has a capability service to render them for).

**Measured rather than assumed.** The decision rested on "a `response: Response`
parameter works alongside a list `response_model`", which was argued from a
sibling route whose `response_model` is a **scalar**. No shipped route paired a
list model with a `Response` param, so before relying on it the pair was run
against this repo's installed FastAPI: `200`, header attached, body still a JSON
array. The claim is now evidence.

**Changed by implementation: the boundary set grew from four to five.** The ADR
and plan enumerated four places where *absence* and *failure* look the same as
*normal*. Implementation added a fifth on the mutation path (**B5**): with no
optimistic update, a clicked radio cannot move until the `PUT` and its refetch
both land, so the control appears inert and invites a second write of a
permission change. Solved the way `MailboxSendControl` already solved it
(`disabled` while pending). The 4x4 derivation grid in the plan
(`{list request, list response, per-row data, mutation request}` x
`{absent, failed, in flight, unexpected}`) exists so a sixth is found by reading
the grid rather than by shipping it.

**A dead branch was removed during code review.** The planned `EmptyState` arm
could not be reached — `connectorsToRender` always returns at least the known
groups — and a rendered case that cannot happen is the same category of untruth
as a control that does nothing.

## Revisit conditions

- **When `CAPABILITY_SERVICE_URLS` is non-empty in any shipped configuration**
  (LUM-622's seed list, or LUM-507's marketplace) — the invisible-enforced-mode
  finding stops being latent and becomes a live user-facing denial.
- **When `check_permission` gains a third mode** (LUM-191 Ask/Do++) — re-check
  that the two-question email grouping still reads correctly with three values,
  and that the store's two-valued column has migrated.
- **When per-action permissions become persistable** (LUM-131's classifier ships
  a key finer than `(user_id, connector)`) — the two-row email grouping becomes
  a summary over per-action rules rather than the rules themselves.
- **When `connector_permissions` gains an instance column for any reason** —
  the "applies to all your mailboxes" line becomes false the moment it does, and
  this ADR's grain decision must be re-argued, not silently overtaken.
- **If a second page ever renders an Ask/Do control** — that is the fragmentation
  this decision exists to prevent; treat it as a regression, not a feature.

## Status history

- 2026-08-25: **Finalised by `/verify-plan`** — implementation confirmed the
  decision. One addition (a fifth absence boundary on the mutation path) and one
  removal (an unreachable empty-state branch); neither changed the decision.
  Finalised copy at `docs/decisions/220-lum-849-autonomy-surface.md`.
- 2026-08-25: Draft created by `/explore` (LUM-849). Supersedes the deferred
  convergence question in `docs/decisions/219-lum-834-connector-enabled-and-field-presence.md`
  decision 8, and corrects three factual errors carried in that decision, in
  LUM-849's body, and in `MeConnectorsView.tsx:120-126`.
