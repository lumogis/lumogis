# ADR-247: Default-install first run — Lumogis Web opens in single-user mode, and first run is the chat zero state, not the onboarding modal (LUM-853)
**Status:** Finalised
**Created:** 2026-09-14
**Last updated:** 2026-11-09
**Decided by:** /explore LUM-853 (2026-09-14); owner decision Thomas 2026-11-08 (Option 2), re-opened and re-decided 2026-11-09 (Path 1 + Path 3); /explore --headless LUM-853 v2 (claude-opus-5-5); revised by /review-plan --arbitrate R1, R2, R3 (claude-opus-5-5)
**Finalised by:** /verify-plan --headless 2026-11-09 (claude-opus-5-5)
**Plan:** `.cursor/plans/LUM-853-chat-first-run-library-empty-state.plan.md`
**Exploration:** `.cursor/explorations/LUM-853-onboarding-auth-disabled-blackout_v2.md` (v1: `…blackout.md`)
**Draft mirror:** `.cursor/adrs/LUM-853-onboarding-auth-disabled-blackout.md`
**Linear:** LUM-853

## Context

`AUTH_ENABLED` defaults to false. `GET` and `PATCH /api/v1/me/onboarding` then return a synthetic `completed_at`, so `OnboardingGate` never mounts. That is how Persona A (Docker) and Persona C (Lumogis Server until sharing is on) run. LUM-282 (ADR 221) already put the paperless offer on `/me/sources` because the modal is dark on those installs. LUM-853 asked what first run should be on such an install.

Dev-mode requests use `UserContext("default")` without a required `users` row. Lighting the modal would mean inventing durable completion for that identity, which collides with the Server sharing bootstrap.

**The premise turned out to be worse than the question (arbitration R1, proven in R2): with auth off, Lumogis Web never reached `/chat`.** Boot calls `POST /api/v1/auth/refresh`, which answers 503 when `AUTH_ENABLED` is not true. `ApiClient.tryRefresh` returns false on any non-OK response, the `AuthProvider` gate became `no_session`, `GET /auth/me` (which would have returned the synthetic `default` admin) was never asked, and `RequireAuth` rendered `LoginForm`, whose login also answers 503 ("Sign-in is disabled in single-user dev mode"). A stack-free Vitest case with the exact auth-off 503 bodies proved it. This had been so since the Web's first commit; no auth-off browser lane existed to notice. The public quickstart nonetheless said a default install needs no `.env` edits and that first-run setup happens in the browser. ADR 244 (LUM-889) and ADR 227 (LUM-881) also assumed the Web was usable with auth off.

Three paths were put to the owner: (1) the Web boots in single-user mode when Core serves it without a credential; (2) a default install turns sign-in on; (3) at minimum, fix the quickstart and the sign-in dead end. **Owner decision (2026-11-09): Path 1 + Path 3**, with the chat empty-library state (Option 2, 2026-11-08) on top. Switching sign-in on later must keep working, which constrains the design: the mode is re-derived on every load and never stored.

## Decision

1. **Lumogis Web runs in single-user mode when, and only when, Core answers an unauthenticated `GET /api/v1/auth/me` with a well-formed admin.** After the boot refresh fails, `AuthProvider` makes one such request through `ApiClient.fetchOnce` (no refresh-and-retry, no `Authorization` header, 5 s timeout). Only a `200` whose body has a non-empty string `id`, a string `email` and `role === "admin"` enters the mode (new internal gate `single_user`; `useAuth().singleUser`). With sign-in on, Core's middleware answers 401 before any route, whatever cookies ride along, so the mode cannot be entered; any other failure (5xx, network, timeout, malformed body) shows the sign-in form. The mode is presentation only: the server authorises every request. It is **never stored** in the browser (only the existing LUM-886 member markers are written, with the value `default`), so turning sign-in on takes effect at the next load, and in an open tab at its next 401 (`onAuthExpired` → sign-out wipe → sign-in form). While in the mode, a non-401 `/auth/me` refetch failure keeps the last good identity rather than dropping to a sign-in form that cannot sign in.
2. **Single-user mode is visibly single-user and cannot add people.** A non-dismissible banner under the shell header on every page says that sign-in is off and that anyone who can reach the address — on this device, the network, or the internet if the address is public — uses Lumogis as its admin, with links to turning sign-in on and to safe remote access. One copy for every hostname: a softer `localhost` copy would be false (Docker publishes on all interfaces), and the client cannot tell a LAN name from a public one. The profile menu reads "Single-user mode" instead of the synthetic `dev@local.lan` and has no Sign out; `logout()` is a no-op; the profile page has no Change password or forgot-password hint and links to turning sign-in on. **Admin → Users is replaced by a notice** and mounts no user, invite or import query or control, because `users` rows created with sign-in off make the next restart refuse to boot (two rows) or stop the bootstrap admin from being created at the switch (one row). The exposure is **warned about, not refused**: today an auth-off Docker install's API (and the legacy `:8000/dashboard`) is already open to whoever can reach the host, the CSRF origin check is off with auth off, the public remote-access guide already prescribes auth for public hostnames, and Core cannot see `CADDY_DOMAIN`; a boot-time refusal belongs to deployment hardening (LUM-887), where Caddy can carry it.
3. **Keep the auth-off synthetic onboarding `completed_at`.** On a single-user install, first run is **in-page zero states**, not `OnboardingModal`. The modal is the orientation for installs with sign-in on. Do not create a `users` row for `default` to show it.
4. **The chat page carries the first-run link.** When the active thread is empty **and** the caller's visible document list (`GET /api/v1/documents`) has **loaded** with zero rows (never while loading, never on error), chat shows the shared `EmptyState` directly after the transcript (outside the `role="log"` live region), with a primary action to `/me/sources` (the LUM-282 offer) and a secondary one to `/documents`. While the library is empty, chat subscribes to document SSE itself (`useDocumentsSseInvalidation(tokens, { enabled })`) so the state flips without a reload, and reads the list without the 2 s busy-poll (`useDocuments(client, { pollWhileBusy: false })`). The condition is the document list, not the auth mode, so a new member with nothing visible gets the same help.
5. **With auth off, wow readiness is real and the dismissal is stored as instance state.** `/me/wow-state` returns `get_wow_state("default")` instead of synthetic readiness (Option E), and the auth-off dismissal is stored under one `app_settings` key (`single_user_wow_dismissed_at`, first dismissal wins), inert once sign-in is on. Both auth-off branches, and the auth-on GET and PATCH response building, map a failure to `503 database_unavailable` (the auth-on branches used to build the response outside their `try`, so an entity-query failure was a 500). `WowGate` is not gated on documents (entities also come from sessions, notes and MCP writes).
6. **The docs and sign-in copy say what a default install is.** The quickstart describes single-user mode and its exposure and gains a **Turn on sign-in** section (anchor `#turn-on-sign-in`, used by the Web's links) listing the settings Core refuses to boot without, the Fernet recipe for `LUMOGIS_CREDENTIAL_KEY`, the restart, and what moves where. The sign-in form's auth-off 503 copy says sign-in is off and to reload, with the same link; it matches the wire literal `AUTH_ERROR_LITERALS.LOGIN_DISABLED_DEV_MODE` for display only — no mode decision reads it. Public copy never names the private Server tree.

This **amends the consequences of ADR 059** (the modal is the sign-in-on first run; the chat zero state is the first run with sign-in off) **and of ADR 221** (the `/me/sources` offer is reached from chat on a single-user install). It makes the Web sentences of **ADR 244** and **ADR 227** true. The switch to sign-in is **ADR 152** (remap of `default` rows to the bootstrap admin) followed by **ADR 154** (invites).

## Alternatives Considered

- **Persist onboarding for `default` (light the modal):** rejected by the owner. It couples first run to the Server sharing seed and contradicts ADR 221.
- **A completion flag kept only in the browser when auth is off:** contradicts ADR 059's server-side completion, and turning sharing on later would bring the modal back.
- **A call to action on every empty thread:** permanent noise, because every new thread is empty.
- **Condition the chat link on no sources:** nags people who upload files or use the inbox, because sources are not documents.
- **Condition the chat link on auth mode:** misses new auth-on members with nothing visible. The Web now knows the mode, but the document condition is still the better one.
- **Hide `WowGate` on an empty library (client-side wow fix):** rejected in R1. It hid real wow cards from members with entities but no documents; Option E fixes the cause instead.
- **Sign-in on by default (Path 2):** rejected 2026-11-09. A zero-edit install would need an admin credential generated and shown at first boot (new secret handling), and a single-user Server install has no household to sign in to.
- **Key single-user mode on the auth-off 503 detail text:** rejected in R2. Human-readable prose is not a contract; the `/auth/me` answer is behaviour the server already guarantees and tests.
- **A new explicit "auth mode" route or flag:** not needed. Both halves of the `/auth/me` signal are pinned by tests; a flag would duplicate it. Kept as the fallback if `/auth/me` ever changes.
- **Remember single-user mode in the browser:** rejected. It would survive the switch to sign-in and show an admin UI that every request then refuses.
- **Refuse to boot with auth off on a public `CADDY_DOMAIN`:** deferred to LUM-887 (Core does not see `CADDY_DOMAIN`; it would break working auth-off installs on a private network; the exposure predates this decision).
- **Hostname-dependent banner copy:** rejected in R3 (a softer `localhost` variant is false; a stronger non-loopback variant needs a public-suffix guess).
- **Store the auth-off wow dismissal in the browser:** rejected for the instance-level `app_settings` key (no new per-member browser store, survives other browsers).

## Consequences

**Easier:**
- A default install opens Lumogis Web (chat), and a person meets document setup on the first screen.
- No new identity, route, env var or stored mode. `test_onboarding_get_dev_synthetic` and `test_onboarding_patch_dev_noop` keep their meaning; `test_wow_get_dev_synthetic` is replaced, because auth-off wow readiness is now real.
- Turning sign-in on needs no client migration: the next load shows the sign-in form; the server remap (ADR 152) moves the data.

**Harder / closed off:**
- Every page of a single-user install carries the sign-in-is-off banner until sign-in is turned on.
- On an install with sign-in on, every anonymous page load makes one extra `GET /api/v1/auth/me` that the middleware rejects before any route.
- On the first sign-in after the switch, the browser's `default` client state (persisted threads, drafts, cache) is wiped by the LUM-886 reconcile, and capture-outbox rows tagged `default` no longer sync (the quickstart says to sync them first).
- Sign-in turned **off** under a tab holding a live member session: the next `/auth/me` refetch answers as `default`, but the tab stays a member tab (no banner, member controls) until a reload re-derives the mode. The server authorises nothing differently; noted in the `AuthProvider.tsx` header.
- The admin user, invite and import routes still accept auth-off calls (`require_admin` is a no-op with auth off); only the Web surface is hidden. Noted at the create-user route.
- Default Docker does not show Welcome unless this ADR is revisited. LUM-204 must attach to a surface that exists with sign-in off, or be sign-in-on only.
- Chat runs one extra cached `GET /api/v1/documents` on mount, shared with `/documents`, and a first-run chat tab holds two long-lived `/api/v1/events` streams while the library is empty.

**Future chunks must know:**
- Nothing in the Web may store or cache the auth mode. Read `useAuth().singleUser`.
- Admin surfaces that create users, invites or imports must stay hidden while `singleUser` is true.
- `OnboardingGate` staying dark when `auth_enabled()` is false is **not** a regression.
- `GET /api/v1/auth/me` without a Bearer must keep answering 401 with sign-in on (even with a refresh cookie) and a well-formed admin with it off; the Web's boot depends on both (pinned in `test_auth_phase1.py`: `test_me_in_dev_mode_returns_synthesised_admin`, `test_me_without_bearer_in_true_mode_returns_401`, `test_me_with_refresh_cookie_but_no_bearer_in_true_mode_returns_401`).
- The chat empty-library state is driven by "no visible documents". If LUM-891 or LUM-893 change what `GET /documents` returns, "empty" still means nothing to ask about. The copy must not claim the account is empty and must not use personal, shared or system.
- No auth-off browser lane exists; the boot and the chat condition are proven in Vitest and by a manual live check (2026-11-09), and a live lane is in LUM-894's Later section.

## Revisit conditions

- The owner requires Welcome on `docker compose up` with a stock `.env`.
- Core changes what `GET /api/v1/auth/me` answers without a credential in either mode.
- LUM-887 adds a boot-time refusal for auth off on a public address (the banner's wording may then change).
- The Lumogis Server sharing bootstrap is redesigned so that a real `users` row always exists with auth off. Lighting the modal is then cheap, and ADR 221 and ADR 227 must be re-argued too.
- LUM-204 is planned and needs a resumable first-run surface with auth off.
- Funnel or support evidence that people on default installs still do not reach `/me/sources` from chat.

## Status history

- 2026-09-14: Draft created by `/explore LUM-853`.
- 2026-11-09: Revised by `/explore --headless LUM-853` (v2). Owner decision 2026-11-08: Option 2, chat link in scope.
- 2026-11-09: Revised during /review-plan --arbitrate R1. Critique R1 found that Lumogis Web cannot boot with auth off; decision suspended pending the owner. Wow handling moved from the client to the backend (Option E).
- 2026-11-09: Revised during /review-plan --arbitrate R2. The premise was proven by a stack-free Vitest probe; Path 2 re-sized. Decision still suspended.
- 2026-11-09: Revised during /review-plan --self R3 after the owner chose Path 1 + Path 3: single-user boot on an unauthenticated `/auth/me` admin answer, never stored; banner (warn, not refuse); hidden sign-out and password change; stored auth-off dismissal; honest quickstart and sign-in copy.
- 2026-11-09: Revised during /review-plan --arbitrate R3: Admin → Users hidden in single-user mode; exposure evidence rewritten; banner names the internet case; 5 s probe timeout; last good identity kept on non-401 `/me` errors. No decision overturned.
- 2026-11-09: Finalised by /verify-plan — implementation confirmed the decision. Folded in implementation detail: the forgot-password hint is hidden with Change password in single-user mode; the auth-on PATCH response building also moved inside its `try` (as GET); the known edge of sign-in turned off under a live member session is recorded. A live check on a throwaway auth-off compose project (re-run by the verifier) confirmed the boot, banner, hidden Admin → Users, empty-library state at 390 px, and the switch to sign-in (401, sign-in form on reload, uploaded document remapped to the bootstrap admin).
- 2026-11-17: Superseded in part by ADR-273 (LUM-981): sign-in is on by default and the first visitor creates the admin, so the rejection of "sign-in on by default" no longer stands, and Decisions 1, 2 and 6 (single-user mode, its banner, the "turn on sign-in" docs) were removed from Lumogis Web. The backend auth-off behaviour and Decision 4 (chat empty-library state) still hold.
