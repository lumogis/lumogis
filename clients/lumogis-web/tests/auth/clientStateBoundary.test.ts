// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-886 — client-state boundary: sign-out wipe, sign-in reconcile, push teardown,
// cross-tab decision. Real fake-indexeddb for drafts and the outbox; only the browser
// push objects are stubbed.

import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../src/api/client";
import { AccessTokenStore } from "../../src/api/tokens";
import {
  isCrossTabSignOut,
  LAST_SIGNED_IN_USER_KEY,
  pingSignedOut,
  reconcileSignedInMember,
  sessionScopedQueryClient,
  SIGNED_OUT_PING_KEY,
  TAB_OWNER_KEY,
  teardownPushForLogout,
  wipeMemberClientState,
} from "../../src/auth/clientStateBoundary";
import { INVITE_ONBOARDING_STORAGE_KEY } from "../../src/api/invites";
import { addTextUrlOutboxItem, listOutboxItems } from "../../src/pwa/captureOutbox";
import { getDraft, makeCaptureDraftKey, makeChatDraftKey, setDraft } from "../../src/pwa/drafts";
import { clear as clearIdb } from "idb-keyval";
import { jsonResponse } from "../helpers/jsonResponse";
import { installServiceWorker, removeServiceWorker } from "../helpers/serviceWorkerStub";

const ME = ["auth", "me"] as const;
const THREADS_KEY = "lumogis-chat-threads";

function newQc(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

beforeEach(async () => {
  await clearIdb();
});

afterEach(() => {
  removeServiceWorker();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("wipeMemberClientState", () => {
  it("removes threads, all lumogis:draft:* keys, the query-cache slot and the email hint", async () => {
    sessionStorage.setItem(THREADS_KEY, "{}");
    await setDraft(makeChatDraftKey("t1"), "chat");
    await setDraft(makeCaptureDraftKey("__quickcapture__"), "capture");
    localStorage.setItem("lumogis:query-cache", "{}");
    localStorage.setItem("lumogis:email:activated", "1");

    const r = await wipeMemberClientState(newQc());

    expect(r.complete).toBe(true);
    expect(sessionStorage.getItem(THREADS_KEY)).toBeNull();
    expect(await getDraft(makeChatDraftKey("t1"))).toBeUndefined();
    expect(await getDraft(makeCaptureDraftKey("__quickcapture__"))).toBeUndefined();
    expect(localStorage.getItem("lumogis:query-cache")).toBeNull();
    expect(localStorage.getItem("lumogis:email:activated")).toBeNull();
  });

  it("keeps the outbox, the login flash and device keys", async () => {
    await addTextUrlOutboxItem({ ownerUserId: "a", kind: "text", text: "unsynced" });
    sessionStorage.setItem("lumogis_login_flash", "Password changed");
    localStorage.setItem("lumogis-theme", "dark");
    localStorage.setItem(LAST_SIGNED_IN_USER_KEY, "a");

    await wipeMemberClientState(newQc());

    expect(await listOutboxItems("a")).toHaveLength(1);
    expect(sessionStorage.getItem("lumogis_login_flash")).toBe("Password changed");
    expect(localStorage.getItem("lumogis-theme")).toBe("dark");
    expect(localStorage.getItem(LAST_SIGNED_IN_USER_KEY)).toBe("a");
  });

  it("clears every query by default", async () => {
    const qc = newQc();
    qc.setQueryData(["signals", "x"], 1);
    qc.setQueryData(ME, { id: "a" });
    await wipeMemberClientState(qc);
    expect(qc.getQueryCache().getAll()).toHaveLength(0);
  });

  it("with keepQueryKey keeps only ['auth','me'] and empties the mutation cache", async () => {
    const qc = newQc();
    qc.setQueryData(["signals", "x"], 1);
    qc.setQueryData(ME, { id: "a" });
    await wipeMemberClientState(qc, { keepQueryKey: ME });
    expect(qc.getQueryCache().getAll().map((q) => q.queryKey)).toEqual([ME]);
    expect(qc.getMutationCache().getAll()).toHaveLength(0);
  });

  it("is single-flight per mode", async () => {
    const qc = newQc();
    const p1 = wipeMemberClientState(qc);
    const p2 = wipeMemberClientState(qc);
    expect(p2).toBe(p1);
    await p1;
  });

  it("a full wipe requested during a keep-wipe still clears ['auth','me']", async () => {
    const qc = newQc();
    const k = wipeMemberClientState(qc, { keepQueryKey: ME });
    qc.setQueryData(ME, { id: "b" });
    const f = wipeMemberClientState(qc);
    expect(f).not.toBe(k);
    await f;
    expect(qc.getQueryData(ME)).toBeUndefined();
  });

  it("a keep-wipe requested during a full wipe runs after it, not instead of it", async () => {
    const qc = newQc();
    const order: string[] = [];
    const realClear = qc.clear.bind(qc);
    const realRemove = qc.removeQueries.bind(qc);
    vi.spyOn(qc, "clear").mockImplementation(() => {
      order.push("clear");
      realClear();
    });
    vi.spyOn(qc, "removeQueries").mockImplementation(((...a: Parameters<typeof qc.removeQueries>) => {
      order.push("removeQueries");
      return realRemove(...a);
    }) as typeof qc.removeQueries);

    const f = wipeMemberClientState(qc);
    const k = wipeMemberClientState(qc, { keepQueryKey: ME });
    expect(k).not.toBe(f);
    await k;
    expect(order).toEqual(["clear", "removeQueries"]);
  });

  it("continues when a step throws, resolves complete:false and warns with the step name only", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    sessionStorage.setItem(THREADS_KEY, "{}");
    await setDraft(makeChatDraftKey("t1"), "chat");
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("denied");
    });

    const r = await wipeMemberClientState(newQc());

    expect(r.complete).toBe(false);
    expect(await getDraft(makeChatDraftKey("t1"))).toBeUndefined();
    expect(warn).toHaveBeenCalledWith("lumogis: client-state wipe step failed", "threads");
    for (const call of warn.mock.calls) {
      expect(call).toHaveLength(2);
      expect(String(call[1])).not.toMatch(/lumogis[:-]|u-/);
    }
  });

  it("the full wipe removes the invite onboarding hint; the keep-mode wipe leaves it", async () => {
    sessionStorage.setItem(INVITE_ONBOARDING_STORAGE_KEY, "{}");
    await wipeMemberClientState(newQc(), { keepQueryKey: ME });
    expect(sessionStorage.getItem(INVITE_ONBOARDING_STORAGE_KEY)).toBe("{}");
    await wipeMemberClientState(newQc());
    expect(sessionStorage.getItem(INVITE_ONBOARDING_STORAGE_KEY)).toBeNull();
  });
});

describe("reconcileSignedInMember", () => {
  const opts = (over: Partial<Parameters<typeof reconcileSignedInMember>[1]> = {}) => ({
    queryClient: newQc(),
    previousUserIdInTab: null,
    keepQueryKey: ME,
    browserTimeoutMs: 50,
    ...over,
  });

  it("with no marker wipes, unsubscribes and writes the marker and tab owner without any request", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const push = installServiceWorker();
    sessionStorage.setItem(THREADS_KEY, "{}");

    const r = await reconcileSignedInMember("u-b", opts());

    expect(r).toEqual({ wiped: true, complete: true });
    expect(push.unsubscribe).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(LAST_SIGNED_IN_USER_KEY)).toBe("u-b");
    expect(sessionStorage.getItem(TAB_OWNER_KEY)).toBe("u-b");
    expect(sessionStorage.getItem(THREADS_KEY)).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("an incomplete wipe writes neither the marker nor the tab owner, and the next reconcile wipes again", async () => {
    localStorage.setItem(LAST_SIGNED_IN_USER_KEY, "u-a");
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const drafts = await import("../../src/pwa/drafts");
    const spy = vi.spyOn(drafts, "clearAllDrafts").mockResolvedValue(false);

    const r = await reconcileSignedInMember("u-b", opts());
    expect(r).toEqual({ wiped: true, complete: false });
    expect(localStorage.getItem(LAST_SIGNED_IN_USER_KEY)).toBe("u-a");
    expect(sessionStorage.getItem(TAB_OWNER_KEY)).toBeNull();

    spy.mockRestore();
    const again = await reconcileSignedInMember("u-b", opts());
    expect(again.wiped).toBe(true);
    expect(localStorage.getItem(LAST_SIGNED_IN_USER_KEY)).toBe("u-b");
  });

  it("with a matching marker and no previous tab user does nothing", async () => {
    const push = installServiceWorker();
    localStorage.setItem(LAST_SIGNED_IN_USER_KEY, "u-a");
    sessionStorage.setItem(TAB_OWNER_KEY, "u-a");
    sessionStorage.setItem(THREADS_KEY, "{}");
    await setDraft(makeChatDraftKey("t"), "keep");

    const r = await reconcileSignedInMember("u-a", opts());

    expect(r.wiped).toBe(false);
    expect(push.unsubscribe).not.toHaveBeenCalled();
    expect(await getDraft(makeChatDraftKey("t"))).toBe("keep");
    expect(sessionStorage.getItem(THREADS_KEY)).toBe("{}");
  });

  it("with a different marker wipes and unsubscribes", async () => {
    const push = installServiceWorker();
    localStorage.setItem(LAST_SIGNED_IN_USER_KEY, "u-a");
    await setDraft(makeChatDraftKey("t"), "a's draft");

    const r = await reconcileSignedInMember("u-b", opts());

    expect(r.wiped).toBe(true);
    expect(push.unsubscribe).toHaveBeenCalledTimes(1);
    expect(await getDraft(makeChatDraftKey("t"))).toBeUndefined();
  });

  it("wipes when this tab's previous user differs even if the marker matches (D6)", async () => {
    localStorage.setItem(LAST_SIGNED_IN_USER_KEY, "u-b");
    sessionStorage.setItem(TAB_OWNER_KEY, "u-b");
    const r = await reconcileSignedInMember("u-b", opts({ previousUserIdInTab: "u-a" }));
    expect(r.wiped).toBe(true);
  });

  it("wipes when the tab owner tag differs, after a reload (D12)", async () => {
    localStorage.setItem(LAST_SIGNED_IN_USER_KEY, "u-b");
    sessionStorage.setItem(TAB_OWNER_KEY, "u-a");
    sessionStorage.setItem(THREADS_KEY, "{\"a\":1}");

    const r = await reconcileSignedInMember("u-b", opts({ previousUserIdInTab: null }));

    expect(r.wiped).toBe(true);
    expect(sessionStorage.getItem(THREADS_KEY)).toBeNull();
    expect(sessionStorage.getItem(TAB_OWNER_KEY)).toBe("u-b");
  });

  it("an unreadable marker falls back to the tab owner", async () => {
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(function (this: Storage, key: string) {
      if (key === LAST_SIGNED_IN_USER_KEY) throw new Error("denied");
      return null;
    });
    sessionStorage.setItem(TAB_OWNER_KEY, "u-a");
    // The mock above returns null for every other key too; read the tab owner explicitly.
    getItem.mockImplementation(function (this: Storage, key: string) {
      if (key === LAST_SIGNED_IN_USER_KEY) throw new Error("denied");
      return Object.getPrototypeOf(this) === Storage.prototype && key === TAB_OWNER_KEY ? "u-a" : null;
    });
    expect((await reconcileSignedInMember("u-a", opts())).wiped).toBe(false);

    getItem.mockImplementation(function (this: Storage, key: string) {
      if (key === LAST_SIGNED_IN_USER_KEY) throw new Error("denied");
      return null;
    });
    expect((await reconcileSignedInMember("u-a", opts())).wiped).toBe(true);
  });

  it("a matching marker and tab owner do nothing and keep threads", async () => {
    localStorage.setItem(LAST_SIGNED_IN_USER_KEY, "u-a");
    sessionStorage.setItem(TAB_OWNER_KEY, "u-a");
    sessionStorage.setItem(THREADS_KEY, "{}");
    const r = await reconcileSignedInMember("u-a", opts());
    expect(r.wiped).toBe(false);
    expect(sessionStorage.getItem(THREADS_KEY)).toBe("{}");
  });

  it("the marker and tab owner hold the user id only; no other key appears", async () => {
    const before = [Object.keys(localStorage), Object.keys(sessionStorage)];
    await reconcileSignedInMember("u-b", opts());
    expect(localStorage.getItem(LAST_SIGNED_IN_USER_KEY)).toBe("u-b");
    expect(sessionStorage.getItem(TAB_OWNER_KEY)).toBe("u-b");
    expect(Object.keys(localStorage).filter((k) => !before[0]!.includes(k))).toEqual([LAST_SIGNED_IN_USER_KEY]);
    expect(Object.keys(sessionStorage).filter((k) => !before[1]!.includes(k))).toEqual([TAB_OWNER_KEY]);
  });

  it("a mismatch closes shown notifications", async () => {
    const push = installServiceWorker();
    localStorage.setItem(LAST_SIGNED_IN_USER_KEY, "u-a");
    await reconcileSignedInMember("u-b", opts());
    for (const n of push.notifications) expect(n.close).toHaveBeenCalledTimes(1);
  });

  it("times out a never-resolving getRegistration and still finishes", async () => {
    installServiceWorker({ getRegistration: () => new Promise(() => {}) });
    const r = await reconcileSignedInMember("u-b", opts({ browserTimeoutMs: 20 }));
    expect(r.wiped).toBe(true);
    expect(localStorage.getItem(LAST_SIGNED_IN_USER_KEY)).toBe("u-b");
  });
});

describe("teardownPushForLogout", () => {
  interface Call {
    path: string;
    method: string;
    signal?: AbortSignal | null;
    token: string | null;
  }

  function setup(handler: (call: Call) => Response | Promise<Response>, sessionUser: string | null = "u-a") {
    const tokens = new AccessTokenStore();
    tokens.set("tok-a");
    const calls: Call[] = [];
    const onAuthExpired = vi.fn();
    const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const call: Call = {
        path: new URL(String(input), "http://x").pathname,
        method: init?.method ?? "GET",
        signal: init?.signal,
        token: tokens.get(),
      };
      calls.push(call);
      return handler(call);
    }) as typeof fetch;
    const client = new ApiClient({ tokens, fetchImpl, onAuthExpired });
    client.setSessionUserId(sessionUser);
    return { client, calls, onAuthExpired, tokens };
  }

  it("calls subscribe, then DELETE, then unsubscribe", async () => {
    const push = installServiceWorker();
    const order: string[] = [];
    push.unsubscribe.mockImplementation(async () => {
      order.push("unsubscribe");
      return true;
    });
    const { client } = setup((c) => {
      order.push(`${c.method} ${c.path}`);
      if (c.path === "/api/v1/notifications/subscribe") return jsonResponse(200, { id: 7, already_existed: true });
      return new Response(null, { status: 204 });
    });
    await teardownPushForLogout(client);
    expect(order).toEqual([
      "POST /api/v1/notifications/subscribe",
      "DELETE /api/v1/notifications/subscriptions/7",
      "unsubscribe",
    ]);
  });

  it("on a 401 refreshes once through tryRefresh and never fires onAuthExpired", async () => {
    // (a) refresh fails
    let push = installServiceWorker();
    let ctx = setup((c) => (c.path.endsWith("/auth/refresh") ? jsonResponse(401, {}) : jsonResponse(401, {})));
    await teardownPushForLogout(ctx.client);
    expect(ctx.calls.filter((c) => c.path === "/api/v1/auth/refresh")).toHaveLength(1);
    expect(ctx.calls.some((c) => c.method === "DELETE")).toBe(false);
    expect(ctx.onAuthExpired).not.toHaveBeenCalled();
    expect(push.unsubscribe).toHaveBeenCalledTimes(1);

    // (b) refresh succeeds as the same member
    push = installServiceWorker();
    let subscribes = 0;
    ctx = setup((c) => {
      if (c.path === "/api/v1/auth/refresh") return jsonResponse(200, { access_token: "tok-2", user: { id: "u-a" } });
      if (c.path === "/api/v1/notifications/subscribe") {
        subscribes += 1;
        return subscribes === 1 ? jsonResponse(401, {}) : jsonResponse(200, { id: 7, already_existed: true });
      }
      return new Response(null, { status: 204 });
    });
    await teardownPushForLogout(ctx.client);
    expect(ctx.calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      "POST /api/v1/notifications/subscribe",
      "POST /api/v1/auth/refresh",
      "POST /api/v1/notifications/subscribe",
      "DELETE /api/v1/notifications/subscriptions/7",
    ]);
    expect(push.unsubscribe).toHaveBeenCalledTimes(1);

    // (c) refresh succeeds as a DIFFERENT member: no second subscribe, no DELETE, token never stored
    push = installServiceWorker();
    const seen: Array<string | null> = [];
    ctx = setup((c) => {
      if (c.path === "/api/v1/auth/refresh") return jsonResponse(200, { access_token: "tok-b", user: { id: "u-b" } });
      return jsonResponse(401, {});
    });
    ctx.tokens.subscribe(() => seen.push(ctx.tokens.get()));
    await teardownPushForLogout(ctx.client);
    expect(ctx.calls.filter((c) => c.path === "/api/v1/notifications/subscribe")).toHaveLength(1);
    expect(ctx.calls.some((c) => c.method === "DELETE")).toBe(false);
    expect(seen).not.toContain("tok-b");
    expect(push.unsubscribe).toHaveBeenCalledTimes(1);
  });

  it("skips DELETE on 503 and still unsubscribes", async () => {
    const push = installServiceWorker();
    const { client, calls } = setup(() => jsonResponse(503, { detail: "webpush_not_configured" }));
    await teardownPushForLogout(client);
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    expect(push.unsubscribe).toHaveBeenCalledTimes(1);
  });

  it("survives a hanging server: aborts the request and still unsubscribes", async () => {
    const push = installServiceWorker();
    let signal: AbortSignal | null | undefined;
    const { client } = setup(
      (c) =>
        new Promise<Response>((_resolve, reject) => {
          signal = c.signal;
          c.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        }),
    );
    await teardownPushForLogout(client, { serverTimeoutMs: 30, browserTimeoutMs: 50 });
    expect(signal?.aborted).toBe(true);
    expect(push.unsubscribe).toHaveBeenCalledTimes(1);
  });

  it("makes no request when there is no subscription", async () => {
    installServiceWorker({ withSubscription: false });
    const { client, calls } = setup(() => jsonResponse(200, {}));
    await teardownPushForLogout(client);
    expect(calls).toHaveLength(0);
  });

  it("is a no-op without navigator.serviceWorker (insecure LAN HTTP)", async () => {
    const { client, calls } = setup(() => jsonResponse(200, {}));
    await expect(teardownPushForLogout(client)).resolves.toBeUndefined();
    expect(calls).toHaveLength(0);
  });

  it("closes shown notifications", async () => {
    const push = installServiceWorker();
    const { client } = setup((c) =>
      c.path.endsWith("/subscribe") ? jsonResponse(200, { id: 1, already_existed: true }) : new Response(null, { status: 204 }),
    );
    await teardownPushForLogout(client);
    for (const n of push.notifications) expect(n.close).toHaveBeenCalledTimes(1);
  });

  it("still unsubscribes when getNotifications-adjacent steps hang", async () => {
    const push = installServiceWorker();
    const { client } = setup(() => jsonResponse(500, {}));
    await teardownPushForLogout(client, { browserTimeoutMs: 30 });
    expect(push.unsubscribe).toHaveBeenCalledTimes(1);
  });
});

describe("cross-tab signals (D13)", () => {
  it("isCrossTabSignOut truth table", () => {
    expect(isCrossTabSignOut({ key: SIGNED_OUT_PING_KEY, newValue: "n" }, "u-a")).toBe(true);
    expect(isCrossTabSignOut({ key: LAST_SIGNED_IN_USER_KEY, newValue: "u-b" }, "u-a")).toBe(true);
    expect(isCrossTabSignOut({ key: LAST_SIGNED_IN_USER_KEY, newValue: "u-a" }, "u-a")).toBe(false);
    expect(isCrossTabSignOut({ key: LAST_SIGNED_IN_USER_KEY, newValue: null }, "u-a")).toBe(false);
    expect(isCrossTabSignOut({ key: null, newValue: null }, "u-a")).toBe(false);
    expect(isCrossTabSignOut({ key: SIGNED_OUT_PING_KEY, newValue: "n" }, null)).toBe(false);
    expect(isCrossTabSignOut({ key: LAST_SIGNED_IN_USER_KEY, newValue: "u-b" }, null)).toBe(false);
    expect(isCrossTabSignOut({ key: "unrelated", newValue: "x" }, "u-a")).toBe(false);
  });

  it("pingSignedOut writes a different value on each call and no user data", () => {
    const values: string[] = [];
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation((k: string, v: string) => {
      if (k === SIGNED_OUT_PING_KEY) values.push(v);
    });
    localStorage.setItem(LAST_SIGNED_IN_USER_KEY, "u-secret-id");
    pingSignedOut();
    pingSignedOut();
    set.mockRestore();
    expect(values).toHaveLength(2);
    expect(values[0]).not.toBe(values[1]);
    for (const v of values) expect(v).not.toContain("u-secret-id");
  });
});

describe("sessionScopedQueryClient (LUM-897)", () => {
  it("reads and writes through until a wipe, then drops data writes; a new handle writes again", async () => {
    const qc = new QueryClient();
    const a = sessionScopedQueryClient(qc);
    expect(a).toBeInstanceOf(QueryClient);
    a.setQueryData(["k"], "a1");
    expect(qc.getQueryData(["k"])).toBe("a1");
    expect(a.getQueryData(["k"])).toBe("a1");
    expect(a.getQueryCache()).toBe(qc.getQueryCache());
    expect(a.setQueryData).toBe(a.setQueryData);

    await wipeMemberClientState(qc);
    expect(a.setQueryData(["k"], "a2")).toBeUndefined();
    expect(a.setQueriesData({ queryKey: ["k"] }, "a3")).toEqual([]);
    expect(qc.getQueryData(["k"])).toBeUndefined();

    const b = sessionScopedQueryClient(qc);
    b.setQueryData(["k"], "b1");
    a.setQueryData(["k"], "a4");
    expect(qc.getQueryData(["k"])).toBe("b1");
    expect(b.setQueriesData({ queryKey: ["k"] }, "b2")).toEqual([[["k"], "b2"]]);
  });

  it("the epoch ends when the wipe is called, before its query step runs", () => {
    const qc = new QueryClient();
    const a = sessionScopedQueryClient(qc);
    void wipeMemberClientState(qc, { keepQueryKey: ["auth", "me"] });
    a.setQueryData(["late"], "a");
    expect(qc.getQueryData(["late"])).toBeUndefined();
  });

  it("a wipe of one client leaves another client's handles live", async () => {
    const one = new QueryClient();
    const two = sessionScopedQueryClient(new QueryClient());
    await wipeMemberClientState(one);
    two.setQueryData(["k"], "v");
    expect(two.getQueryData(["k"])).toBe("v");
  });
});
