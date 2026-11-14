# ADR-242: Lumogis Web — per-member client state at sign-out and member switch (LUM-886)
**Status:** Finalised
**Created:** 2026-11-08
**Last updated:** 2026-11-08
**Decided by:** claude-opus-5-5 (/explore --headless LUM-886), revised in /review-plan arbitration R1
**Finalised by:** /verify-plan --headless 2026-11-08 (claude-opus-5-5)
**Plan:** `.cursor/plans/LUM-886-web-signout-client-state.plan.md`
**Exploration:** `.cursor/explorations/LUM-886-web-signout-client-state.md`
**Draft mirror:** `.cursor/adrs/LUM-886-web-signout-client-state.md`
**Linear:** LUM-886

## Context

A security review found that Lumogis Web's logout cleared only the in-memory access token and the `['auth','me']` query. On a shared household device the next member then saw the previous member's chat threads (`sessionStorage["lumogis-chat-threads"]`), which are sent to the model as context. The next member also saw and synced the previous member's capture outbox (IndexedDB, voice audio included) and drafts, saw their cached queries, and the device kept the previous member's push subscription.

Four facts constrain the fix:

- A session does not only end through logout. `onAuthExpired` fires on any failed refresh, a Core restart included, and a session that expires while the browser is closed fails at boot without running any handler.
- The capture outbox is the only copy of offline captures.
- Drafts and the outbox share idb-keyval's default store.
- The client does not keep the server id of its push subscription.

## Decision

Lumogis Web has one **client-state boundary module** (`clients/lumogis-web/src/auth/clientStateBoundary.ts`), called from `AuthProvider`. It owns which browser state belongs to a signed-in member.

- **On sign-out**, both explicit logout and auth-expiry, it **wipes the ephemeral state**:
  - the chat-threads sessionStorage key;
  - every `lumogis:draft:*` IndexedDB key, deleted by prefix, never by `clear()`;
  - the `QueryClient` (`clear()`) and its persisted localStorage slot;
  - the email-activation hint and the invite onboarding hint.

  The wipe runs in a post-commit effect after the auth gate flips, so unmounting pages flush their draft writes first and the delete wins.
- **Wipes are serialised by mode.** A sign-out wipe (`clear()` everything) and a sign-in reconcile wipe (keep `['auth','me']`) never stand in for each other. Each step is caught on its own; the run reports `{ complete }`. A failing step logs its name only.
- It **keeps the capture outbox**. Each outbox row carries `owner_user_id`; list, stats, sync, discard and the voice clip-count cap act only on the signed-in member's rows (the voice byte cap stays device-wide). Rows from before this change have no owner: they are never auto-synced, and are shown apart with a short preview to be claimed ("Sync to my account") or discarded.
- **On explicit logout**, before `POST /auth/logout`: an idempotent re-POST to `/notifications/subscribe` returns this member's row id, the client `DELETE`s it (one identity-checked refresh on a 401), closes notifications on screen, and always calls `PushSubscription.unsubscribe()`. Every step is bounded; failures are ignored. The logout POST itself is aborted after 3 s, and `logout()` is single-flight.
- **On sign-in**, before any child renders (`status` stays `loading`), it compares a device marker (`localStorage["lumogis:last-signed-in-user"]`, user id only), a per-tab owner tag (`sessionStorage["lumogis:tab-owner"]`, survives a reload) and the provider's previous user with the new user. If any differs, or the marker is absent (unknown previous member: fail closed), it runs the keep-`/me` wipe, closes shown notifications and unsubscribes any leftover push subscription. The marker and tag are written only after a complete wipe. A new sign-in waits for a pending sign-out wipe.
- **Across tabs**, a `storage` event on the marker (a different member signed in) or on an explicit-logout ping key (`localStorage["lumogis:signed-out-ping"]`, a random nonce) signs the other open tabs out locally, with no server calls.
- **`ApiClient` knows whose session it holds** (`setSessionUserId` / `getSessionUserId`). A refresh that returns a different member is a failed refresh: its token is never stored, the request is not replayed, and the tab signs out. A refresh or a 401 that belongs to a session that has since ended never signs out or clears the token of the session that replaced it, and an in-flight refresh is shared only within one session. The capture-outbox sync checks the live session immediately before every network call and stops (restoring the row) when it is no longer the rows' owner.

No hard reload, no `Clear-Site-Data`, no backend change.

## Alternatives Considered

Full detail: `.cursor/explorations/LUM-886-web-signout-client-state.md`.

- **Hard reload after logout.** sessionStorage survives a same-tab reload, and IndexedDB and push survive any reload, so it fixes only the query cache.
- **`Clear-Site-Data: "storage"` on logout.** Secure-context only (LAN HTTP installs get nothing). It also unregisters the service worker and destroys offline captures and the password-change flash message.
- **Wipe everything, including the outbox, on logout and auth-expiry.** Loses offline captures when a refresh fails for a transient reason, and misses the expiry-at-boot path.
- **Owner-scope every key and wipe nothing.** Leaves every member's chat text at rest on a shared profile indefinitely.
- **`BroadcastChannel` for cross-tab sign-out.** Not needed: `storage` events have the same reach on keys this module already writes.
- **A self-registering cleaner registry.** A store module not yet imported (lazy route) would not register, and the wipe would silently skip it. A central list plus a source-scanning storage-inventory test (`tests/auth/storageInventory.test.ts`) makes a forgotten store fail CI instead.

## Consequences

**Easier:**
- One place answers "what does sign-out clear". A new per-member client store adds one step there, and the storage-inventory test fails until a new storage-touching file is classified. This is the contract for LUM-205 (chat interface) and LUM-207 (quick capture UI).
- The member-switch check makes isolation hold even when logout never ran.

**Harder / accepted:**
- A member's unsynced captures stay at rest in the browser profile until that member syncs or discards them.
- Logout on a personal device switches push off until the member re-enables it.
- A tab that misses the `storage` event (frozen or discarded) is caught at its reload (tab tag), `/me` (reconcile) or refresh (identity check).
- When a reconcile wipe is incomplete (IndexedDB failing), the marker is not written, so other live tabs get no `storage` event; they are caught at their next refresh or reconcile. Such a profile re-wipes at every load.
- The first sign-in on each device after the update wipes drafts and chat-tab threads once and switches push off once.
- Captures saved before the update have no owner; any member can claim one (with a short preview) or discard it.
- When the push DELETE runs with an already-revoked session (password change), the server row is left for the push service's 404/410 prune.
- Worst-case logout latency is bounded at roughly 8.5 s (push teardown ≈ 5.5 s plus the 3 s logout POST), and only when a push subscription exists and Core or the browser hangs.

**Future chunks must know:**
- Never call `sessionStorage.clear()`, `localStorage.clear()` or idb-keyval `clear()` for sign-out. `lumogis_login_flash`, theme preferences and the outbox depend on that.
- Any authenticated call made during logout may 401 (the password-change flow revokes the session first); use `fetchOnce` for anything that must run during logout.
- Any future transport that refreshes outside `ApiClient.fetch` (SSE reconnect, a worker) must apply the same identity rule.
- New code must read the signed-in member through `useAuth().user` (exposed only after the reconcile), not `queryClient.getQueryData(["auth","me"])`.

## Revisit conditions

- LUM-205 moves chat threads fully server-side and drops the sessionStorage mirror: remove that step and keep the boundary.
- A household device mode with per-member browser profiles or OS accounts ships: the sign-in marker can relax.
- Lumogis adds a "delete push subscription by endpoint" route: replace the re-POST-then-DELETE step.
- The query-persistence allowlist gains a user-specific key: confirm the persisted-slot removal still runs on both paths. Also fix the buster, which is `package.json` `0.0.1` and never changes (noted at `getQueryPersistenceBuster`).
- Offline capture moves to a native queue (LUM-117) or a sync engine (LUM-219): carry the owner-tag and member-switch rules over.

## Status history
- 2026-11-08: Draft created by /explore --headless (LUM-886)
- 2026-11-08: Revised during /review-plan --arbitrate R1 — cross-tab sign-out moved into the decision (`storage` events); per-tab owner tag added; `ApiClient` refresh identity check and outbox session-liveness guard recorded; absent marker = unknown member made explicit.
- 2026-11-08: Finalised by /verify-plan — implementation confirmed the decision. Folded in the implementation detail (wipe serialised by mode, post-commit wipe effect, single-flight bounded logout, sign-in waits for a pending wipe, outbox discard owner guard). Verification fixed one gap: a refresh failing across a session change no longer signs out or clears the next member's session, and an in-flight refresh is no longer shared across sessions.
