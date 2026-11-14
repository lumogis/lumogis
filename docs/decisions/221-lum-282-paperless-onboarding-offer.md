# ADR-221: Paperless onboarding — the offer lives on the sources surface, not in the modal (LUM-282)
**Status:** Finalised (canonical copy: `docs/decisions/221-lum-282-paperless-onboarding-offer.md`)
**Created:** 2026-09-03
**Last updated:** 2026-09-03
**Decided by:** `/explore LUM-282`

## Context

LUM-282's remaining scope is "an optional, skippable onboarding step that offers
paperless setup during first run", handing off to the shipped connector card and
source add flow rather than reimplementing either. Everything it hands off to has
shipped: `PaperlessForm` (LUM-834 G2), the per-user `/me/sources` add flow with
both consent gates (LUM-837 / LUM-839), and source health with poll error codes.

Two facts about the shipped first-run surface shaped the option space, and both
were read off the code rather than assumed:

1. **The one existing handoff of exactly this shape is broken.**
   `ConnectSourcesStep`'s `Open connectors` button calls `navigate("/me/connectors")`
   while `ModalFrame` keeps `#root` `inert` and `aria-hidden`. The route changes
   behind an overlay that never closes. Proven by a throwaway Vitest: after the
   click, one dialog is still open, `#root` is still inert. The user's only exits —
   Skip, Done, ESC — all permanently complete onboarding, because `PATCH
   /me/onboarding` is one-way and accepts `completed: true` and nothing else. No
   test presses that button.

2. **The modal is dark on a default install.** `auth_enabled()` defaults to false
   (`orchestrator/auth.py:61`); `me.py:275` then returns a synthetic `completed_at`,
   so `OnboardingGate` renders nothing. This is the default for Persona A (Docker)
   *and* Persona C (Lumogis Server ships `AUTH_ENABLED=false` until household
   sharing is enabled — `sharing.rs:22`, `env.bundled.template:13`). A first-run
   offer built only into the modal would be invisible to both personas the ticket
   names.

## Decision

Deliver LUM-282's offer on **`/me/sources`**, as a real zero state built from the
shared `EmptyState` component LUM-165 authored as a contract — replacing
`SourcesView.tsx:176`'s `<p>No sources yet.</p>` — and **re-aim the existing
`ConnectSourcesStep`** at `/me/sources` instead of `/me/connectors`, completing
onboarding as it navigates so the handoff is an honest exit rather than a trap.
No new modal page, no new route, column, migration, env var, dependency or Docker
service. Where the offer must be cut for scope, the modal half goes and the zero
state stays.

**Revised 2026-09-03 (R1 arbitration):** a third edit belongs in this Decision and
was missing from it — **`PaperlessForm` gains one line saying where the paperless
API token comes from**, wired to the token input with `aria-describedby`. Both
critics flagged that the plan carried an edit neither this ADR nor the ticket
authorised (LUM-282 lists *"the typed paperless form"* under **Not in scope**,
assigning it to LUM-834). They were right that it was unrecorded. It stays, on an
explicit owner decision: the token field is the single point every entry path
converges on, so an offer that dead-ends at a bare "API token" label fails the
persona the ticket names, and it fails them whether they arrived through onboarding
or straight from `/me/connectors`. LUM-834 is **Done**, so this is not contended
scope, and the change is copy plus one ARIA attribute — no payload key, validation
or submit behaviour is touched.

## Alternatives Considered

- **A distinct paperless page in `OnboardingModal`** — the literal reading of the
  ticket, and the weakest option: invisible on a default install, inherits the
  broken handoff, and grows the exact structure LUM-204's identity wizard must
  later own. See `.cursor/explorations/LUM-282-paperless-onboarding-step.md` Option 1.
- **Resumable onboarding** (step pointer or pending-task set, so the handoff is a
  detour) — correct medicine for a wizard that collects input; Lumogis's steps
  collect nothing but a button press, so it buys a migration and no visible gain.
  Revisit under LUM-204. Option 2.
- **A persistent dismissible "finish setting up" card** — needs a new per-user
  dismissal column (`wow_dismissed_at` is bespoke, not a generic facility) for a
  nag the zero state achieves by disappearing when a source exists.
- **Embedding the paperless form in the modal** — forbidden by the ticket ("no
  third implementation") and by ADR-215 D4, whose credential/source two-step is
  already a *state on the sources screen* (`AddSourcePanel.PaperlessCredentialStep`).
- **Product-tour libraries** — already rejected by ADR-059 / LUM-165.

## Consequences

**Easier:**
- The offer reaches every install, not only households that enabled sharing.
- No half-configured state is possible: the zero state is derived from "this user
  has no sources", so it clears itself and needs no flag to remember a skip.
- `/me/sources` stops being the grey "No integrations found" dead end and starts
  being the surface that explains the credential/source two-step it already
  implements.
- A shipped, untested defect gets a test that currently fails.

**Harder / foreclosed:**
- Per-connector first-run *modal pages* are closed off as a growth pattern. The
  next connector wanting a first-run offer gets a zero state on its own surface.
- "Onboarding is resumable" is not an assumption anything may rely on; a feature
  needing it must build the state first.
- Completing onboarding on handoff means the sharing-on user forgoes the Done
  screen. Accepted by the owner 2026-09-03: the button states its destination, and
  Skip already ends onboarding silently today.
- The modal keeps four steps. `ConnectSourcesStep` is rewritten in place rather
  than joined by a fifth page — chosen so LUM-204 inherits the structure it
  already expects.

**Future chunks must know:**
- **Focus does not follow a cross-route close, and *trying* is not enough.**
  Implementation found the second half of this: `SourcesView` cannot simply call
  `.focus()`. When the handoff arrives the modal is usually still mounted — the
  gate unmounts it a macrotask later — so `#root` still carries `inert`, and a
  real browser refuses focus inside an inert subtree. The heading ref therefore
  **verifies** the attempt landed and retries once after the unmount.
  **jsdom does not enforce `inert`** (measured), so no Vitest test in this repo
  can distinguish the working code from the broken code; the Playwright
  assertion on `document.activeElement` is the only guard. Any future surface
  that takes focus on arrival from a modal handoff inherits both facts.
- **`location.state` belongs to the history entry, not to the visit.** The
  handoff's `fromOnboarding` flag must be scrubbed after first use
  (`navigate(pathname, { replace: true, state: null })`), or every later return
  to that entry — Back, forward, a restored session — re-triggers the arrival
  behaviour on a visit the user did not initiate.
- **"One intent, one write" needs two guards, not a disabled button.**
  `disabled={busy}` gates buttons only; ESC reaches the completion through
  `ModalFrame`'s document-level keydown with no button involved. And `busy`
  clears when the request settles, while the modal lives one macrotask longer —
  so the button is briefly re-enabled *on screen* after success. The shipped
  code carries `busyRef` (concurrency) and `handoffDoneRef` (post-success).
- **Closing this dialog by navigating is new, and focus does not follow.**
  `ModalFrame`'s cleanup restores focus with `prev?.isConnected && prev.focus()`
  (`modalFrame.tsx:92`), which assumes same-page dismissal. After a cross-route
  close the previous element is disconnected, the guard fails, and
  `document.activeElement` falls back to `<body>`. Any future step that hands off
  to another surface must focus something on the destination deliberately — there
  is no app-wide route-focus convention to inherit.
- **The handoff must navigate only after the completion `PATCH` resolves.**
  `completeOnboarding` uses `mutateAsync` and rejects on failure;
  `OnboardingModal.finish()` has `try/finally` and no `catch`, and both call sites
  `void` it — so a failed `PATCH` today produces an unhandled rejection while the
  modal correctly stays up (proven, exploration §Consequences a). Writing the
  handoff as `void finish(); navigate(...)` would navigate on the failure path and
  strand the user behind an open, `inert`-holding modal: Finding 1 reintroduced by
  its own fix. Navigate on success only; on failure stay and render `completeError`.
  Test the rejecting path, not only the happy one.
- The handoff button must join the `busy` set that already disables
  Back/Skip/Next/Done, or a double click issues two `PATCH`es and two navigations.
- `PATCH /api/v1/me/onboarding` accepts `completed: true` **only** (`me.py:312`).
  There is no un-complete and no step pointer. Any "replay onboarding" work is a
  backend change first.
- The `EmptyState` props contract (`title`, `helperText`, ordered
  `actions: [{label, href|onClick, primary?}]`) is ADR-059's, shared with
  `DocumentsPage` and `DocumentChatPage` — consume it, do not fork it.
- The credentials link inside `AddSourcePanel` opens in a **new tab on purpose**:
  in-app navigation unmounts the panel and takes the preview and any consent
  acknowledgements with it. Do not "tidy" it into a `<Link>`.
- Onboarding copy written here says "your paperless address and token" in the
  singular; LUM-626 (multi-instance credentials) must amend it.

## Revisit conditions

- **LUM-204 (identity wizard) is planned** — a 7-question wizard *does* collect
  input, so the resumable-onboarding option this ADR declines becomes the right
  answer for that flow, and the fate of `ConnectSourcesStep` must be decided there.
- **The `AUTH_ENABLED=false` onboarding blackout is fixed** — if the modal starts
  rendering on default installs, the ranking in this ADR (zero state primary,
  modal step secondary) should be re-argued rather than inherited.
- **LUM-626 ships multi-instance paperless credentials** — the singular copy and
  the single-row presence check both need re-scoping.
- **A second connector wants a first-run offer** — if three surfaces end up with
  near-identical zero states, extract the pattern rather than repeating it a fourth
  time.

## Status history
- 2026-09-03: **Finalised by `/verify-plan`** — implementation confirmed the
  decision. The offer shipped on `/me/sources` as specified, the modal step was
  re-aimed and completes onboarding on handoff, and `PaperlessForm` carries the
  token hint. Three implementation-time discoveries were folded into
  Consequences (inert blocking focus, history-state persistence, and the second
  write guard); none of them changed the Decision. Canonical copy at
  `docs/decisions/221-lum-282-paperless-onboarding-offer.md`.
- 2026-09-03: Draft created by `/explore LUM-282`.
- 2026-09-03: **Revised during `/review-plan --arbitrate` R1.** Added the
  `PaperlessForm` token hint to the Decision — both critics correctly found it in
  the plan and in neither this ADR nor the ticket's scope. Recorded rather than
  dropped, on the owner decision taken during planning. Also added a consequence
  the Decision had not considered: this is the **first** change to close the
  onboarding dialog via a cross-route navigation, which is the one case
  `ModalFrame`'s focus restoration (`prev?.isConnected && prev.focus()`) was not
  written for — so the handoff must hand focus somewhere deliberate on the
  destination page.
- 2026-09-03: Both open questions settled by the owner — **upgrade the existing
  `ConnectSourcesStep`** rather than add a distinct paperless page, and **complete
  onboarding on handoff** rather than build resumable onboarding. Recorded here and
  in the exploration's `## Consequences of those two answers`, with the failure-path
  hazard those answers create proven by a second throwaway Vitest. Ready for
  `/create-plan`.
