# ADR-251: Members' pages write to the query cache through a per-session handle (LUM-897)
**Status:** Finalised
**Created:** 2026-11-10
**Last updated:** 2026-11-10
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-11-10 (claude-opus-5-5)
**Plan:** none — shipped as a targeted `exec:claude` session without a plan / verify cycle
**Exploration:** `.cursor/explorations/query_cache_session_epoch_retro.md`
**Draft mirror:** `.cursor/adrs/query_cache_session_epoch.md`
**Linear:** LUM-897
**Amends:** [ADR 242](242-lum-886-web-signout-client-state.md) (per-member client state at sign-out and member switch)

## Context

ADR 242 (LUM-886) wipes the query cache when a member signs out or a different member signs in, on the one long-lived `QueryClient` that `AuthProvider` creates. TanStack Query v5 still runs a mutation's `onSuccess` / `onError` / `onSettled` after its component unmounted and after the mutation cache was cleared. Those callbacks call `setQueryData` on the client they captured from `useQueryClient()`, which by then serves the next member. A slow save by member A (notification preferences, quiet hours, onboarding, wow-state, admin settings) could therefore write A's data into member B's cache. `['me','onboarding']` has a 5-minute `staleTime`, so B could skip onboarding because A had finished it. `ApiClient`'s session check (`sentAs`) does not help: it only guards refresh and replay on a 401. Found by the weekly code review (range `7e100140..3395a2d2`).

## Decision

**Session-epoch guard.** Lumogis Web keeps one `QueryClient`, but members' pages never get the raw client.

- `clientStateBoundary.ts` keeps a session epoch per `QueryClient` (`WeakMap`). Every `wipeMemberClientState` call ends the epoch **synchronously, before** its steps run. A stale write that lands before the wipe's "queries" step is removed by it. A stale write that lands after is dropped.
- `sessionScopedQueryClient(queryClient)` returns a `Proxy` handle bound to the current epoch. Reads, subscriptions and every other method are forwarded to the client, bound to it because `QueryClient` keeps its state in `#private` fields. `setQueryData` and `setQueriesData` do nothing once the handle's epoch has ended; they return `undefined` and `[]`. `instanceof QueryClient` still holds.
- `AuthProviderCore` wraps `children` in an inner `QueryClientProvider` whose client is the current handle. It renews the handle in `markReconciled(id)` for a signed-in member (the reconcile's wipe has already run) and when a sign-out wipe finishes. The provider itself keeps the raw client for `['auth','me']`, the wipes and persistence.
- Every `src/` writer reaches the cache through `useQueryClient()`, so **no feature file changed**. A writer added later is covered by default.

### As-implemented surface

- `clients/lumogis-web/src/auth/clientStateBoundary.ts`: `sessionScopedQueryClient`, `SESSION_GUARDED_WRITES`, epoch bump at the top of `wipeMemberClientState`.
- `clients/lumogis-web/src/auth/AuthProvider.tsx`: `sessionClient` state, `renewSessionClient`, inner `QueryClientProvider`.

## Alternatives Considered

- Not chosen: **a new `QueryClient` per session, with the provider keyed by session.** `AuthProviderCore` reads `['auth','me']` from the same client, `PersistQueryClientProvider` would re-run its restore on every client change, and keying the subtree would remount the public pages (`/invite`, `/forgot-password`) on sign-in, which `InviteRedemptionPage` does not expect after `adoptSession`. The handle gives the same "old closure → dead client" property without that churn.
- Not chosen: **guarding each writer** (a `useSessionGuard()` hook at the call sites). It touches every feature file, and the next writer that forgets it reopens the leak.
- Not chosen: **wrapping mutation callbacks in a custom `MutationCache.build`.** `MutationObserver.setOptions` replaces a mutation's options with unwrapped ones on every re-render, and it would not cover non-mutation async writers.
- Not chosen: **guarding `invalidateQueries` / `fetchQuery` / `prefetchQuery` too.** They run their `queryFn` under the next member's token, so they fetch that member's own data, not the previous member's.

## Consequences

- A mutation or async callback started under one member cannot write data into the cache once the session has changed, including optimistic-update rollbacks.
- `useQueryClient()` returns a new object after each session change, so effects that list the client as a dependency run once more at sign-in. Those components are under `RequireAuth` and remount anyway.
- Pages outside `RequireAuth` that stay mounted across a sign-in hold the previous handle in observers created before the wipe. Their reads work; their direct writes from callbacks made before the re-render are dropped. No public page writes query data today.
- Not covered by design: code that reaches past the handle (`getQueryCache().find(…).setData`), and module-level `QueryClient` references. None exist in `src/`. The limits are recorded in the code next to `SESSION_GUARDED_WRITES`.
- A future per-member store that writes through TanStack Query needs no extra registration. A store that writes anywhere else still registers in `clientStateBoundary.ts` as ADR 242 says.

## Revisit conditions

- A feature writes to the cache from outside React (a module-level `queryClient`, a service-worker bridge, or a `getQueryCache()` write). Extend the guard or route the write through the handle.
- Lumogis Web moves to a TanStack Query major that changes `QueryClient` internals (private fields, `QueryClientProvider` mount/unmount) or adds new data-writing client methods. Re-check the proxy and `SESSION_GUARDED_WRITES`.
- A public page (outside `RequireAuth`) starts writing query data across a sign-in.

## Linear linkage (Product OS)

- **Existing issue:** LUM-897.
- **New issue needed:** no.
- **Historical evidence only:** no. The fix is on branch `claude/lum-897`, not yet merged into `dev`.

## Testing retrospective

- **Added:** `tests/auth/signOutBoundary.test.tsx` § "a previous member's in-flight mutation cannot write into the next member's cache (LUM-897)", 4 cases using the real `AuthProvider` + `RequireAuth`: an onSuccess writer, an optimistic onError rollback writer (plus an `onSettled` `setQueriesData`), the real `useOnboardingStatus` hook, and B's own write surviving A's stale write. All 4 failed before the fix with the issue's symptom (`expected { quiet_hours: "A's quiet hours" } to be undefined`). Also `tests/auth/clientStateBoundary.test.ts` § `sessionScopedQueryClient`, 3 unit cases.
- **Run:** Vitest `tests/auth/` (6 files, 93 passed), full `npm test` (165 files, 1582 passed, 0 failed), `npm run lint` (exit 0), `npm run codegen && npm run build` (exit 0).
- **Gaps:** no live-browser (Playwright) run of the slow-save path. The Vitest cases drive the real provider, so a browser run would add little.

## Status history

- 2026-11-10: Finalised by /record-retro (retrospective).
