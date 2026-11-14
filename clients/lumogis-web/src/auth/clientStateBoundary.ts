// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Client-state boundary (LUM-886): which browser state belongs to the signed-in
// household member, and what is removed when a member's session ends or a different
// member signs in on the same device.
//
// This module is the ONLY place that decides sign-out cleanup. Every new per-member
// client store registers here: an ephemeral store adds a step to
// `wipeMemberClientState`; an "only copy" store (the capture outbox) carries an owner id
// and filters by it instead. `tests/auth/storageInventory.test.ts` fails CI when a new
// file touches browser storage without being classified.
//
// NEVER call `sessionStorage.clear()`, `localStorage.clear()` or idb-keyval `clear()`
// here or anywhere in `src/`: they would destroy `lumogis_login_flash` (the password
// change message), theme/sidebar preferences and the capture outbox, which can hold the
// only copy of an unsynced capture. Remove keys by name or prefix only.
//
// Imports only pure store helpers and TanStack types — no React.
//
// LUM-897 — session epoch. Wiping the query cache is not enough: TanStack still runs a
// mutation's onSuccess / onError after its component unmounted and after the mutation
// cache was cleared, and those callbacks write with the client they captured. So every
// wipe ends the client's session epoch, and the members' pages never get the raw client:
// `AuthProvider` hands them a `sessionScopedQueryClient` handle, renewed per session, whose
// data writes are dropped once its epoch has ended.

import { hashKey, type QueryClient, type QueryKey } from "@tanstack/react-query";

import { deleteOwnWebPushRowForSubscription } from "../api/webPush";
import type { ApiClient } from "../api/client";
import { clearInviteOnboardingHint } from "../api/invites";
import { clearCachedEmailActivated } from "../features/email/activation";
import { clearPersistedThreads } from "../features/chat/threadStore";
import { clearAllDrafts } from "../pwa/drafts";
import { clearPersistedQueryCache } from "../pwa/queryPersistence";
import {
  closeShownNotifications,
  getExistingBrowserPushSubscription,
  unsubscribeBrowserPush,
} from "../pwa/webPushBrowser";

/** Device marker: the member who last completed a sign-in reconcile. User id only. */
export const LAST_SIGNED_IN_USER_KEY = "lumogis:last-signed-in-user";
/** Per-tab owner tag (sessionStorage, survives a reload — D12). User id only. */
export const TAB_OWNER_KEY = "lumogis:tab-owner";
/** Cross-tab sign-out signal (D13): a fresh random nonce, no user data. */
export const SIGNED_OUT_PING_KEY = "lumogis:signed-out-ping";

const DEFAULT_BROWSER_TIMEOUT_MS = 1000;
const DEFAULT_SERVER_TIMEOUT_MS = 2500;
const IDB_WIPE_TIMEOUT_MS = 5000;

export interface WipeOptions {
  /** Keep this one query (the reconcile keeps `['auth','me']`). Omit → full `queryClient.clear()`. */
  keepQueryKey?: QueryKey;
}

/** `complete` is false when any step failed (the failure is swallowed, never thrown). */
export interface WipeResult {
  complete: boolean;
}

// --- session epoch (LUM-897) ------------------------------------------------

const sessionEpochs = new WeakMap<QueryClient, number>();

function epochOf(queryClient: QueryClient): number {
  return sessionEpochs.get(queryClient) ?? 0;
}

/**
 * Methods of a session handle that put data into the cache, with what they return once
 * the handle's session has ended. Covers every `src/` writer (all go through
 * `useQueryClient()`). Not covered, by design: reaching past the handle
 * (`getQueryCache().find(…).setData`) and `fetchQuery` / `prefetchQuery` (their queryFn
 * runs under the next member's token, so it fetches that member's own data). Invalidate
 * and refetch calls stay live for the same reason.
 */
const SESSION_GUARDED_WRITES: Record<string, () => unknown> = {
  setQueryData: () => undefined,
  setQueriesData: () => [],
};

/**
 * A handle on `queryClient` for one member's session. It reads and subscribes like the
 * client itself, but `setQueryData` / `setQueriesData` do nothing once a wipe has run
 * since the handle was made, so a slow callback of the previous member cannot write into
 * the next member's cache. Make a new handle after each wipe; `instanceof QueryClient`
 * still holds.
 */
export function sessionScopedQueryClient(queryClient: QueryClient): QueryClient {
  const epoch = epochOf(queryClient);
  const bound = new WeakMap<object, unknown>();
  return new Proxy(queryClient, {
    get(target, prop) {
      // QueryClient keeps its state in #private fields, so methods must run on the target.
      const value: unknown = Reflect.get(target, prop, target);
      if (typeof value !== "function") return value;
      const cached = bound.get(value);
      if (cached) return cached;
      const method = value as (...args: unknown[]) => unknown;
      const fallback = typeof prop === "string" ? SESSION_GUARDED_WRITES[prop] : undefined;
      const fn = fallback
        ? (...args: unknown[]) => (epochOf(target) === epoch ? method.apply(target, args) : fallback())
        : method.bind(target);
      bound.set(value, fn);
      return fn;
    },
  });
}

// --- wipe -------------------------------------------------------------------

// Wipes are serialised by mode: a sign-out wipe ("full") must still `clear()` everything
// when it arrives during a reconcile wipe ("keep:…"), and a reconcile wipe must still
// keep the new member's `/me` when it arrives during a sign-out wipe.
let wipeTail: Promise<unknown> = Promise.resolve();
let lastWipe: { mode: string; promise: Promise<WipeResult> } | null = null;

function warnStep(step: string): void {
  // Step name only — never a key, a value or a user id.
  console.warn("lumogis: client-state wipe step failed", step);
}

async function runWipe(queryClient: QueryClient, opts: WipeOptions | undefined): Promise<WipeResult> {
  let complete = true;
  const step = async (name: string, fn: () => boolean | void | Promise<boolean | void>): Promise<void> => {
    try {
      const ok = await fn();
      if (ok === false) {
        complete = false;
        warnStep(name);
      }
    } catch {
      complete = false;
      warnStep(name);
    }
  };

  const keep = opts?.keepQueryKey;
  await step("threads", () => clearPersistedThreads());
  // Bounded: a hung IndexedDB open must not wedge every later wipe, the splash or login.
  await step("drafts", () => bounded(clearAllDrafts(), IDB_WIPE_TIMEOUT_MS, false));
  await step("queries", () => {
    if (keep) {
      const keepHash = hashKey(keep);
      queryClient.removeQueries({ predicate: (q) => hashKey(q.queryKey) !== keepHash });
      queryClient.getMutationCache().clear();
    } else {
      queryClient.clear();
    }
  });
  await step("query-cache", () => clearPersistedQueryCache());
  await step("email-hint", () => clearCachedEmailActivated());
  if (!keep) {
    // Full (sign-out) wipe only: the reconcile must keep the invite hint, because
    // `InviteRedemptionPage` writes it for the invitee just after `adoptSession`.
    await step("invite-hint", () => clearInviteOnboardingHint());
  }
  return { complete };
}

/** Remove the previous member's ephemeral state. Single-flight per mode. Never throws. */
export function wipeMemberClientState(queryClient: QueryClient, opts?: WipeOptions): Promise<WipeResult> {
  // LUM-897: end the session epoch first, synchronously. A stale write that lands before
  // the "queries" step below is removed by it; one that lands later is dropped.
  sessionEpochs.set(queryClient, epochOf(queryClient) + 1);
  const mode = opts?.keepQueryKey ? `keep:${hashKey(opts.keepQueryKey)}` : "full";
  if (lastWipe && lastWipe.mode === mode) return lastWipe.promise;

  const promise: Promise<WipeResult> = wipeTail.then(() => runWipe(queryClient, opts)).catch(
    () => ({ complete: false }),
  );
  wipeTail = promise;
  const entry = { mode, promise };
  lastWipe = entry;
  void promise.then(() => {
    if (lastWipe === entry) lastWipe = null;
  });
  return promise;
}

// --- cross-tab sign-out (D13) -------------------------------------------------

function randomNonce(): string {
  try {
    const id = globalThis.crypto?.randomUUID?.();
    if (id) return id;
  } catch {
    /* insecure context */
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** Explicit logout only: other tabs of this browser sign out. Errors ignored. */
export function pingSignedOut(): void {
  try {
    globalThis.localStorage?.setItem(SIGNED_OUT_PING_KEY, randomNonce());
  } catch {
    /* storage disabled */
  }
}

/** D13 decision for one `storage` event. Pure; `AuthProvider` performs the sign-out. */
export function isCrossTabSignOut(
  ev: Pick<StorageEvent, "key" | "newValue">,
  reconciledUserId: string | null,
): boolean {
  if (reconciledUserId === null) return false;
  if (ev.key === SIGNED_OUT_PING_KEY) return true;
  return ev.key === LAST_SIGNED_IN_USER_KEY && ev.newValue !== null && ev.newValue !== reconciledUserId;
}

// --- bounded helpers ------------------------------------------------------------

function bounded<T>(work: Promise<T>, ms: number, fallback: T): Promise<T> {
  return new Promise<T>((resolve) => {
    const timer = setTimeout(() => resolve(fallback), ms);
    work.then(
      (v) => {
        clearTimeout(timer);
        resolve(v);
      },
      () => {
        clearTimeout(timer);
        resolve(fallback);
      },
    );
  });
}

async function currentBrowserSubscription(ms: number): Promise<PushSubscription | null> {
  return bounded(getExistingBrowserPushSubscription(), ms, null);
}

// --- push teardown (explicit logout) ---------------------------------------------

/**
 * Explicit logout only, before `POST /auth/logout` (the access token is still live).
 * Deletes this member's server row for the browser's push subscription when the session
 * is valid or refreshable, closes notifications on screen, and ALWAYS unsubscribes the
 * browser. Every step is bounded and failures are ignored. Never throws.
 */
export async function teardownPushForLogout(
  client: ApiClient,
  opts: { serverTimeoutMs?: number; browserTimeoutMs?: number } = {},
): Promise<void> {
  const browserMs = opts.browserTimeoutMs ?? DEFAULT_BROWSER_TIMEOUT_MS;
  const serverMs = opts.serverTimeoutMs ?? DEFAULT_SERVER_TIMEOUT_MS;
  try {
    const sub = await currentBrowserSubscription(browserMs);
    if (!sub) return;

    const controller = new AbortController();
    const serverStep = async (): Promise<void> => {
      const out = await deleteOwnWebPushRowForSubscription(client, sub, controller.signal);
      if (out === "unauthorized") {
        // Idle device: the 900 s access token expired but the refresh session may live.
        // `tryRefresh` is identity-checked (D15) and never fires `onAuthExpired`.
        const refreshed = await client.tryRefresh();
        if (refreshed) await deleteOwnWebPushRowForSubscription(client, sub, controller.signal);
      }
    };
    await bounded(
      serverStep().catch(() => undefined),
      serverMs,
      undefined,
    );
    controller.abort();

    await bounded(closeShownNotifications(), browserMs, undefined);
    await bounded(unsubscribeBrowserPush(sub), browserMs, false);
  } catch {
    /* never block logout */
  }
}

// --- sign-in reconcile --------------------------------------------------------------

function readMarker(): { ok: true; value: string | null } | { ok: false } {
  try {
    return { ok: true, value: globalThis.localStorage.getItem(LAST_SIGNED_IN_USER_KEY) };
  } catch {
    return { ok: false };
  }
}

function readTabOwner(): string | null {
  try {
    return globalThis.sessionStorage.getItem(TAB_OWNER_KEY);
  } catch {
    return null;
  }
}

function writeOwnerKeys(userId: string): void {
  try {
    globalThis.localStorage.setItem(LAST_SIGNED_IN_USER_KEY, userId);
  } catch {
    /* ignored */
  }
  try {
    globalThis.sessionStorage.setItem(TAB_OWNER_KEY, userId);
  } catch {
    /* ignored */
  }
}

/**
 * Run before `RequireAuth` renders children for `userId`. Wipes the previous member's
 * ephemeral state (keeping `['auth','me']`) when the device marker, this tab's owner tag
 * or this provider's previous user says someone else was here — or when the marker is
 * absent (unknown previous member: fail closed). Then closes shown notifications and
 * unsubscribes any leftover browser push subscription. Never throws.
 */
export async function reconcileSignedInMember(
  userId: string,
  opts: {
    queryClient: QueryClient;
    previousUserIdInTab: string | null;
    keepQueryKey: QueryKey;
    browserTimeoutMs?: number;
  },
): Promise<{ wiped: boolean; complete: boolean }> {
  // Reads happen synchronously, before the first await (StrictMode runs this twice).
  const marker = readMarker();
  const tabOwner = readTabOwner();
  const markerMismatch = marker.ok ? marker.value !== userId : tabOwner !== userId;
  const mustWipe =
    markerMismatch ||
    (tabOwner !== null && tabOwner !== userId) ||
    (opts.previousUserIdInTab !== null && opts.previousUserIdInTab !== userId);

  let complete = true;
  if (mustWipe) {
    try {
      complete = (await wipeMemberClientState(opts.queryClient, { keepQueryKey: opts.keepQueryKey })).complete;
    } catch {
      complete = false;
    }
    const ms = opts.browserTimeoutMs ?? DEFAULT_BROWSER_TIMEOUT_MS;
    await bounded(closeShownNotifications(), ms, undefined);
    const sub = await currentBrowserSubscription(ms);
    await bounded(unsubscribeBrowserPush(sub), ms, false);
  }
  // An incomplete wipe leaves both keys as they were, so the next sign-in or reload retries.
  // Known gap (LUM-886 verify): skipping the marker write also skips the D13 `storage`
  // event, so another live tab still holding the previous member is not signed out at
  // once; it is caught at its next refresh (D15, ≤ the access-token TTL) or reconcile.
  // Only reachable when IndexedDB fails, which also re-wipes on every load here.
  if (!mustWipe || complete) writeOwnerKeys(userId);
  return { wiped: mustWipe, complete: !mustWipe || complete };
}

/** Vitest only: reset the wipe serialisation state. */
export function __resetClientStateBoundaryForTests(): void {
  wipeTail = Promise.resolve();
  lastWipe = null;
}
