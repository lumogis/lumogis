// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-886 — AuthProvider-level proof that one member's browser state does not reach the
// next member on a shared device. Real AuthProvider + RequireAuth, real fake-indexeddb
// for drafts and the outbox; only the network and the push objects are stubbed.

import { StrictMode, useEffect, type ReactNode } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, useMutation, useQueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { clear as clearIdb, keys as idbKeys } from "idb-keyval";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../src/api/client";
import { INVITE_ONBOARDING_STORAGE_KEY } from "../../src/api/invites";
import { AccessTokenStore } from "../../src/api/tokens";
import { AuthProvider, RequireAuth, useAuth, type AuthValue } from "../../src/auth/AuthProvider";
import {
  LAST_SIGNED_IN_USER_KEY,
  SIGNED_OUT_PING_KEY,
  TAB_OWNER_KEY,
} from "../../src/auth/clientStateBoundary";
import { ChatPage } from "../../src/features/chat/ChatPage";
import { useOnboardingStatus } from "../../src/features/onboarding/useOnboardingStatus";
import { addAudioOutboxItem, addTextUrlOutboxItem, listOutboxItems, syncOutbox } from "../../src/pwa/captureOutbox";
import * as draftsModule from "../../src/pwa/drafts";
import { getDraft, makeCaptureDraftKey, makeChatDraftKey, setDraft } from "../../src/pwa/drafts";
import { jsonResponse } from "../helpers/jsonResponse";
import { installServiceWorker, removeServiceWorker } from "../helpers/serviceWorkerStub";
import { seedSignedInDevice } from "../helpers/signedInDevice";

const A = { id: "u-a", email: "a@home.lan", role: "user" as const };
const B = { id: "u-b", email: "b@home.lan", role: "user" as const };
const THREADS_KEY = "lumogis-chat-threads";

interface Call {
  path: string;
  method: string;
  auth: string | null;
  signal?: AbortSignal | null;
}

interface Net {
  calls: Call[];
  /** What `POST /auth/refresh` answers. Default: 401. */
  refresh: () => Response;
  /** What `POST /auth/logout` answers. Default: 200. */
  logout: (init?: RequestInit) => Response | Promise<Response>;
  /** Anything else under /api/v1. Default: 404. */
  other: (call: Call, init?: RequestInit) => Response | Promise<Response>;
  handler: typeof fetch;
}

function loginBody(user: typeof A | typeof B) {
  return { access_token: user === A ? "tok-a" : "tok-b", token_type: "bearer" as const, expires_in: 900, user };
}

function makeNet(): Net {
  const calls: Call[] = [];
  const net: Net = {
    calls,
    refresh: () => jsonResponse(401, { detail: "no session" }),
    logout: () => jsonResponse(200, {}),
    other: () => jsonResponse(404, { detail: "unexpected" }),
    handler: (async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), "http://x").pathname;
      const call: Call = {
        path,
        method: init?.method ?? "GET",
        auth: new Headers(init?.headers).get("Authorization"),
        signal: init?.signal,
      };
      calls.push(call);
      if (path === "/api/v1/auth/login") {
        const { email } = JSON.parse(String(init?.body)) as { email: string };
        return jsonResponse(200, loginBody(email.startsWith("a@") ? A : B));
      }
      if (path === "/api/v1/auth/me") {
        if (call.auth === "Bearer tok-a") return jsonResponse(200, A);
        if (call.auth === "Bearer tok-b") return jsonResponse(200, B);
        return jsonResponse(401, { detail: "unauthenticated" });
      }
      if (path === "/api/v1/auth/refresh") return net.refresh();
      if (path === "/api/v1/auth/logout") return net.logout(init);
      return net.other(call, init);
    }) as typeof fetch,
  };
  return net;
}

interface Env {
  net: Net;
  client: ApiClient;
  tokens: AccessTokenStore;
  auth: { current: AuthValue | null };
  qc: { current: QueryClient | null };
  childRenders: { current: number };
}

function Spy({ env }: { env: Env }): null {
  const auth = useAuth();
  const qc = useQueryClient();
  env.auth.current = auth;
  env.qc.current = qc;
  return null;
}

function Child({ env, onMount }: { env: Env; onMount?: () => void }): JSX.Element {
  const { user } = useAuth();
  env.childRenders.current += 1;
  useEffect(() => {
    onMount?.();
  }, [onMount]);
  return <div data-testid="child">{user?.email}</div>;
}

let originalFetch: typeof fetch;

function mount(opts: {
  net?: Net;
  startAs?: typeof A | typeof B | null;
  /** Seed the device marker + tab owner for this member (default: the startAs member). */
  deviceOwner?: string | null;
  skipRefreshOnMount?: boolean;
  strict?: boolean;
  child?: (env: Env) => ReactNode;
}): Env {
  const net = opts.net ?? makeNet();
  const tokens = new AccessTokenStore();
  const client = new ApiClient({ tokens, fetchImpl: net.handler });
  const env: Env = {
    net,
    client,
    tokens,
    auth: { current: null },
    qc: { current: null },
    childRenders: { current: 0 },
  };
  globalThis.fetch = net.handler;
  const startAs = opts.startAs === undefined ? A : opts.startAs;
  const owner = opts.deviceOwner === undefined ? (startAs ? startAs.id : null) : opts.deviceOwner;
  if (owner) seedSignedInDevice(owner);
  if (startAs) tokens.set(startAs === A ? "tok-a" : "tok-b");
  const tree = (
    <MemoryRouter>
      <AuthProvider client={client} tokens={tokens} skipRefreshOnMount={opts.skipRefreshOnMount ?? true}>
        <Spy env={env} />
        <RequireAuth>{opts.child ? opts.child(env) : <Child env={env} />}</RequireAuth>
      </AuthProvider>
    </MemoryRouter>
  );
  render(opts.strict ? <StrictMode>{tree}</StrictMode> : tree);
  return env;
}

async function seedMemberState(ownerId: string): Promise<{ outboxIds: string[] }> {
  sessionStorage.setItem(THREADS_KEY, JSON.stringify({ v: 1, threads: [{ id: "t-a" }] }));
  await setDraft(makeChatDraftKey("t-a"), "a's chat draft");
  await setDraft(makeCaptureDraftKey("__quickcapture__"), "a's capture draft");
  localStorage.setItem("lumogis:query-cache", "{}");
  localStorage.setItem("lumogis:email:activated", "1");
  const t = await addTextUrlOutboxItem({ ownerUserId: ownerId, kind: "text", text: "a's unsynced note" });
  const au = await addAudioOutboxItem({
    ownerUserId: ownerId,
    blob: new Blob([new Uint8Array([1, 2])], { type: "audio/webm" }),
    mimeType: "audio/webm",
  });
  return { outboxIds: [t.local_capture_id, au.local_capture_id] };
}

async function draftKeys(): Promise<string[]> {
  return (await idbKeys()).filter((k): k is string => typeof k === "string" && k.startsWith("lumogis:draft:"));
}

/**
 * `logout()` resolves only after the post-commit wipe effect ran, so it must NOT be awaited
 * inside `act` (React holds effects back until the act scope exits → deadlock).
 */
async function signOut(env: Env): Promise<void> {
  const p = env.auth.current!.logout();
  // Two bounded 1 s browser probes can run when getRegistration never answers.
  await waitFor(() => expect(loginFormVisible()).toBe(true), { timeout: 4000 });
  await p;
}

async function signIn(env: Env, email: string): Promise<void> {
  const p = env.auth.current!.login(email, "a-long-enough-password");
  await screen.findByTestId("child");
  await p;
}

function loginFormVisible(): boolean {
  return screen.queryByRole("heading", { name: /sign in/i }) !== null;
}

beforeEach(async () => {
  originalFetch = globalThis.fetch;
  await clearIdb();
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  removeServiceWorker();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("sign-out hands the next member a clean device", () => {
  it("A → logout → B: nothing of A's remains (asserted on the login form, before B signs in)", async () => {
    const env = mount({ startAs: A });
    await screen.findByTestId("child");
    const { outboxIds } = await seedMemberState(A.id);
    env.qc.current!.setQueryData(["signals", "mine"], { secret: "a" });

    await signOut(env);

    // --- on the login form, before the next sign-in (so the reconcile cannot mask a gap)
    await waitFor(() => expect(loginFormVisible()).toBe(true));
    expect(sessionStorage.getItem(THREADS_KEY)).toBeNull();
    expect(await getDraft(makeChatDraftKey("t-a"))).toBeUndefined();
    expect(await getDraft(makeCaptureDraftKey("__quickcapture__"))).toBeUndefined();
    expect(env.qc.current!.getQueryData(["signals", "mine"])).toBeUndefined();
    expect(env.qc.current!.getQueryCache().getAll().filter((q) => q.state.data !== undefined)).toHaveLength(0);
    expect(localStorage.getItem("lumogis:query-cache")).toBeNull();
    expect(localStorage.getItem("lumogis:email:activated")).toBeNull();
    expect(localStorage.getItem(LAST_SIGNED_IN_USER_KEY)).toBe(A.id);
    // the only copy of unsynced captures is NOT wiped
    expect(await listOutboxItems(A.id)).toHaveLength(2);

    // --- B signs in
    await signIn(env, "b@home.lan");
    await screen.findByTestId("child");
    expect(await listOutboxItems(B.id)).toHaveLength(0);
    env.net.calls.length = 0;
    const r = await syncOutbox(env.client, B.id);
    expect(r).toMatchObject({ synced: 0, failed: 0 });
    expect(env.net.calls.filter((c) => c.path === "/api/v1/captures")).toHaveLength(0);
    expect(env.qc.current!.getQueryData(["signals", "mine"])).toBeUndefined();
    expect(await listOutboxItems(A.id)).toHaveLength(2);
    expect((await listOutboxItems(A.id)).map((m) => m.local_capture_id).sort()).toEqual([...outboxIds].sort());
  });

  it("logout order: push subscribe → DELETE → unsubscribe → /auth/logout", async () => {
    const order: string[] = [];
    const push = installServiceWorker();
    push.unsubscribe.mockImplementation(async () => {
      order.push("unsubscribe");
      return true;
    });
    const net = makeNet();
    net.other = (c) => {
      order.push(`${c.method} ${c.path}`);
      if (c.path === "/api/v1/notifications/subscribe") return jsonResponse(200, { id: 9, already_existed: true });
      return new Response(null, { status: 204 });
    };
    net.logout = () => {
      order.push("POST /api/v1/auth/logout");
      return jsonResponse(200, {});
    };
    const env = mount({ net, startAs: A });
    await screen.findByTestId("child");

    await signOut(env);

    expect(order).toEqual([
      "POST /api/v1/notifications/subscribe",
      "DELETE /api/v1/notifications/subscriptions/9",
      "unsubscribe",
      "POST /api/v1/auth/logout",
    ]);
  });

  it.each([
    ["the subscribe call rejects", (n: Net) => (n.other = () => Promise.reject(new TypeError("network")))],
    [
      "DELETE answers 500",
      (n: Net) =>
        (n.other = (c) =>
          c.method === "DELETE" ? jsonResponse(500, {}) : jsonResponse(200, { id: 1, already_existed: true })),
    ],
    ["unsubscribe throws", (_n: Net, push: ReturnType<typeof installServiceWorker>) => push.unsubscribe.mockRejectedValue(new Error("x"))],
  ])("each failing push step still lets logout finish: %s", async (_name, arrange) => {
    const push = installServiceWorker();
    const net = makeNet();
    arrange(net, push);
    const env = mount({ net, startAs: A });
    await screen.findByTestId("child");

    await signOut(env);

    await waitFor(() => expect(loginFormVisible()).toBe(true));
    expect(net.calls.some((c) => c.path === "/api/v1/auth/logout")).toBe(true);
  });

  it("a never-answering getRegistration still lets logout finish", async () => {
    installServiceWorker({ getRegistration: () => new Promise(() => {}) });
    const env = mount({ startAs: A });
    await screen.findByTestId("child");
    await signOut(env);
    await waitFor(() => expect(loginFormVisible()).toBe(true));
    expect(env.net.calls.some((c) => c.path === "/api/v1/auth/logout")).toBe(true);
  });

  it("auth-expired wipes threads and drafts but does not touch push, the outbox or the ping", async () => {
    const push = installServiceWorker();
    const env = mount({ startAs: A });
    await screen.findByTestId("child");
    await seedMemberState(A.id);
    env.net.calls.length = 0;

    await act(async () => {
      await env.client.fetch("/api/v1/anything"); // 404 → not an expiry
      await env.client.fetch("/api/v1/auth/me", { headers: { Authorization: "Bearer stale" } }); // 401 → refresh 401 → expiry
    });

    await waitFor(() => expect(loginFormVisible()).toBe(true));
    await waitFor(async () => expect(await draftKeys()).toHaveLength(0));
    expect(sessionStorage.getItem(THREADS_KEY)).toBeNull();
    expect(push.unsubscribe).not.toHaveBeenCalled();
    expect(env.net.calls.some((c) => c.path.startsWith("/api/v1/notifications"))).toBe(false);
    expect(await listOutboxItems(A.id)).toHaveLength(2);
    expect(localStorage.getItem(SIGNED_OUT_PING_KEY)).toBeNull();
  });

  it("a 401 during logout (password-change flow) finishes once and keeps the login flash", async () => {
    sessionStorage.setItem("lumogis_login_flash", "Password changed — sign in again");
    const net = makeNet();
    const env = mount({ net, startAs: A });
    await screen.findByTestId("child");
    await setDraft(makeChatDraftKey("t-a"), "draft");
    net.logout = async () => {
      // The session is already revoked: an authenticated call 401s, refresh fails.
      await env.client.fetch("/api/v1/auth/me", { headers: { Authorization: "Bearer stale" } });
      return jsonResponse(200, {});
    };

    const unhandled = vi.fn();
    process.on("unhandledRejection", unhandled);
    await signOut(env);
    process.off("unhandledRejection", unhandled);

    await waitFor(() => expect(loginFormVisible()).toBe(true));
    expect(await draftKeys()).toHaveLength(0);
    expect(unhandled).not.toHaveBeenCalled();
    expect(await screen.findByText("Password changed — sign in again")).toBeInTheDocument();
  });

  it("a hung POST /auth/logout is aborted at 3 s and sign-out still completes", async () => {
    const net = makeNet();
    let signal: AbortSignal | null | undefined;
    net.logout = (init) =>
      new Promise<Response>((_res, reject) => {
        signal = init?.signal;
        init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      });
    const env = mount({ net, startAs: A });
    await screen.findByTestId("child");
    await setDraft(makeChatDraftKey("t-a"), "draft");

    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    let done = false;
    const p = env.auth.current!.logout().then(() => {
      done = true;
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    vi.useRealTimers();
    await waitFor(() => expect(loginFormVisible()).toBe(true));
    await p;

    expect(done).toBe(true);
    expect(signal?.aborted).toBe(true);
    await waitFor(() => expect(loginFormVisible()).toBe(true));
    expect(await draftKeys()).toHaveLength(0);
  });

  it("a double-tap on Sign out runs one teardown and one /auth/logout", async () => {
    installServiceWorker();
    const net = makeNet();
    net.other = (c) =>
      c.path === "/api/v1/notifications/subscribe"
        ? jsonResponse(200, { id: 3, already_existed: true })
        : new Response(null, { status: 204 });
    const env = mount({ net, startAs: A });
    await screen.findByTestId("child");

    const p1 = env.auth.current!.logout();
    const p2 = env.auth.current!.logout();
    expect(p2).toBe(p1);
    await waitFor(() => expect(loginFormVisible()).toBe(true));
    await Promise.all([p1, p2]);

    expect(net.calls.filter((c) => c.path === "/api/v1/notifications/subscribe")).toHaveLength(1);
    expect(net.calls.filter((c) => c.path === "/api/v1/auth/logout")).toHaveLength(1);
  });

  it("an unmounting ChatPage's draft write loses to the wipe (real ChatPage)", async () => {
    const net = makeNet();
    net.other = (c) => {
      if (c.path === "/api/v1/models") {
        return jsonResponse(200, {
          models: [{ id: "ollama-mistral", label: "Ollama", is_local: true, enabled: true, provider: "ollama" }],
        });
      }
      if (c.path === "/api/v1/conversations") return jsonResponse(200, { conversations: [] });
      if (c.path === "/api/v1/me/wow-state") {
        return jsonResponse(200, {
          entities_ready: false,
          top_entities: [],
          wow_dismissed_at: null,
          onboarding_completed_at: "2026-01-01T00:00:00Z",
        });
      }
      if (c.path === "/api/v1/me/privacy-mode") {
        return jsonResponse(200, {
          instance: { privacy_mode: "local_only", privacy_mode_locked: false, privacy_effective: "local_only" },
          user_restriction: "inherit",
          privacy_effective: "local_only",
          can_allow_cloud: false,
        });
      }
      return jsonResponse(404, {});
    };
    const user = userEvent.setup();
    const env = mount({ net, startAs: A, child: () => <ChatPage /> });
    const composer = await screen.findByRole("textbox");
    // Inside the 400 ms debounce: the only draft write is the unmount write racing the wipe.
    await user.type(composer, "half-typed secret");

    await signOut(env);

    await waitFor(() => expect(loginFormVisible()).toBe(true));
    expect(await draftKeys()).toHaveLength(0);
  });

  it("a synthetic unmount-time draft write loses to the wipe", async () => {
    function Writer(): null {
      useEffect(() => {
        return () => {
          void setDraft(makeChatDraftKey("late"), "written while unmounting");
        };
      }, []);
      return null;
    }
    const env = mount({
      startAs: A,
      child: () => (
        <>
          <Writer />
          <div data-testid="child" />
        </>
      ),
    });
    await screen.findByTestId("child");
    await signOut(env);
    await waitFor(() => expect(loginFormVisible()).toBe(true));
    expect(await draftKeys()).toHaveLength(0);
  });
});

describe("sign-in reconcile", () => {
  it("boot with an expired A session and a live B session: B never sees A's draft, A's outbox survives", async () => {
    seedSignedInDevice(A.id);
    await setDraft(makeChatDraftKey("t-a"), "a's draft");
    const out = await addTextUrlOutboxItem({ ownerUserId: A.id, kind: "text", text: "a's note" });
    const net = makeNet();
    net.refresh = () => jsonResponse(200, loginBody(B));
    const seen: Array<string | undefined> = [];
    const env = mount({
      net,
      startAs: null,
      deviceOwner: A.id,
      skipRefreshOnMount: false,
      child: (e) => (
        <Child
          env={e}
          onMount={() => {
            void getDraft(makeChatDraftKey("t-a")).then((d) => seen.push(d));
          }}
        />
      ),
    });
    await screen.findByTestId("child");
    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]).toBeUndefined();
    expect(await listOutboxItems(A.id)).toHaveLength(1);
    expect(await listOutboxItems(B.id)).toHaveLength(0);

    await signOut(env);
    await signIn(env, "a@home.lan");
    await screen.findByTestId("child");
    expect((await listOutboxItems(A.id)).map((m) => m.local_capture_id)).toEqual([out.local_capture_id]);
  });

  it("holds the splash until the reconcile finishes, and renders after", async () => {
    let release!: (v: unknown) => void;
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    const push = installServiceWorker({ getRegistration: () => gate as Promise<undefined> });
    void push;
    const env = mount({ startAs: B, deviceOwner: A.id });

    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    expect(screen.queryByTestId("child")).toBeNull();

    release(undefined);
    await screen.findByTestId("child");
    expect(screen.queryByText("Loading…")).toBeNull();
    expect(env.auth.current!.user?.id).toBe(B.id);
  });

  it("the same member signing back in does not unsubscribe push again and finds their outbox row", async () => {
    const push = installServiceWorker();
    const env = mount({ startAs: A });
    await screen.findByTestId("child");
    await addTextUrlOutboxItem({ ownerUserId: A.id, kind: "text", text: "mine" });
    await signOut(env);
    push.unsubscribe.mockClear();

    await signIn(env, "a@home.lan");
    await screen.findByTestId("child");

    expect(push.unsubscribe).not.toHaveBeenCalled();
    expect(await listOutboxItems(A.id)).toHaveLength(1);
  });

  it("adoptSession for another member (invite flow) wipes, remounts the child and keeps the invite hint", async () => {
    const env = mount({ startAs: A });
    await screen.findByTestId("child");
    sessionStorage.setItem(THREADS_KEY, "{\"a\":1}");
    localStorage.setItem(LAST_SIGNED_IN_USER_KEY, B.id); // a previous sign-in of B on this device
    const before = env.childRenders.current;

    act(() => {
      env.auth.current!.adoptSession(loginBody(B));
      sessionStorage.setItem(INVITE_ONBOARDING_STORAGE_KEY, "{\"allows_shared\":true}");
    });

    await waitFor(() => expect(env.auth.current!.user?.id).toBe(B.id));
    expect(env.childRenders.current).toBeGreaterThan(before);
    expect(sessionStorage.getItem(THREADS_KEY)).toBeNull();
    expect(sessionStorage.getItem(INVITE_ONBOARDING_STORAGE_KEY)).toBe("{\"allows_shared\":true}");
  });

  it("a reloaded stale tab never rehydrates another member's threads (D12)", async () => {
    sessionStorage.setItem(THREADS_KEY, "{\"a\":1}");
    sessionStorage.setItem(TAB_OWNER_KEY, A.id);
    localStorage.setItem(LAST_SIGNED_IN_USER_KEY, B.id);
    const net = makeNet();
    net.refresh = () => jsonResponse(200, loginBody(B));
    const seen: Array<string | null> = [];
    mount({
      net,
      startAs: null,
      deviceOwner: null,
      skipRefreshOnMount: false,
      child: (e) => <Child env={e} onMount={() => seen.push(sessionStorage.getItem(THREADS_KEY))} />,
    });
    await screen.findByTestId("child");
    expect(seen[0]).toBeNull();
    expect(localStorage.getItem(LAST_SIGNED_IN_USER_KEY)).toBe(B.id);
    expect(sessionStorage.getItem(TAB_OWNER_KEY)).toBe(B.id);
  });

  it("StrictMode: the child renders once reconciled and only one wipe runs", async () => {
    const spy = vi.spyOn(draftsModule, "clearAllDrafts");
    const env = mount({ startAs: B, deviceOwner: A.id, strict: true });
    await screen.findByTestId("child");
    expect(spy).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(LAST_SIGNED_IN_USER_KEY)).toBe(B.id);
    expect(env.auth.current!.user?.id).toBe(B.id);
  });

  it("login during a pending sign-out wipe waits for it and ends signed in as B (D17)", async () => {
    let release!: () => void;
    const hold = new Promise<void>((resolve) => {
      release = resolve;
    });
    const spy = vi.spyOn(draftsModule, "clearAllDrafts").mockImplementation(async () => {
      await hold;
      return true;
    });
    const env = mount({ startAs: A });
    await screen.findByTestId("child");

    let loggingOut!: Promise<void>;
    act(() => {
      loggingOut = env.auth.current!.logout();
    });
    await waitFor(() => expect(spy).toHaveBeenCalled());

    let loggingIn!: Promise<unknown>;
    act(() => {
      loggingIn = env.auth.current!.login("b@home.lan", "a-long-enough-password");
    });
    await new Promise((r) => setTimeout(r, 20));
    // B's /me must not be written while the previous member's wipe is still pending.
    expect((env.qc.current!.getQueryData(["auth", "me"]) as { id?: string } | undefined)?.id).not.toBe(B.id);

    release();
    await Promise.all([loggingOut, loggingIn]);
    await screen.findByTestId("child");
    expect(env.auth.current!.user?.id).toBe(B.id);
    expect(env.qc.current!.getQueryData(["auth", "me"])).toMatchObject({ id: B.id });
  });
});

describe("a refresh that comes back as another member (D11 / D15)", () => {
  it("is not replayed: the tab is signed out, A's state is wiped, B's token is never sent", async () => {
    const net = makeNet();
    const env = mount({ net, startAs: A });
    await screen.findByTestId("child");
    await seedMemberState(A.id);
    net.refresh = () => jsonResponse(200, loginBody(B));
    net.other = () => jsonResponse(401, { detail: "expired" });
    net.calls.length = 0;

    await act(async () => {
      await env.client.fetch("/api/v1/chat/something", { method: "POST" });
    });

    await waitFor(() => expect(loginFormVisible()).toBe(true));
    expect(net.calls.filter((c) => c.path === "/api/v1/chat/something")).toHaveLength(1);
    expect(net.calls.some((c) => c.auth === "Bearer tok-b")).toBe(false);
    expect(sessionStorage.getItem(THREADS_KEY)).toBeNull();
    await waitFor(async () => expect(await draftKeys()).toHaveLength(0));
    expect(await listOutboxItems(A.id)).toHaveLength(2);
  });
});

describe("cross-tab sign-out over storage events (D13)", () => {
  function fire(key: string, newValue: string | null): void {
    window.dispatchEvent(new StorageEvent("storage", { key, newValue }));
  }

  it("another tab signing a different member in signs this tab out, with no server calls", async () => {
    const env = mount({ startAs: A });
    await screen.findByTestId("child");
    await seedMemberState(A.id);
    env.net.calls.length = 0;

    act(() => fire(LAST_SIGNED_IN_USER_KEY, B.id));

    await waitFor(() => expect(loginFormVisible()).toBe(true));
    await waitFor(async () => expect(await draftKeys()).toHaveLength(0));
    expect(sessionStorage.getItem(THREADS_KEY)).toBeNull();
    expect(env.net.calls.some((c) => c.path.startsWith("/api/v1/notifications") || c.path === "/api/v1/auth/logout")).toBe(
      false,
    );
  });

  it("the marker event for the same member does nothing", async () => {
    mount({ startAs: A });
    await screen.findByTestId("child");
    act(() => fire(LAST_SIGNED_IN_USER_KEY, A.id));
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.getByTestId("child")).toBeInTheDocument();
  });

  it("an explicit logout elsewhere (ping) signs this tab out; logout here writes a new ping", async () => {
    const env = mount({ startAs: A });
    await screen.findByTestId("child");
    await seedMemberState(A.id);
    act(() => fire(SIGNED_OUT_PING_KEY, "nonce-1"));
    await waitFor(() => expect(loginFormVisible()).toBe(true));
    await waitFor(async () => expect(await draftKeys()).toHaveLength(0));
    expect(env.auth.current!.status).toBe("anonymous");
  });

  it("logout() writes the ping", async () => {
    const pings: string[] = [];
    const real = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, k: string, v: string) {
      if (k === SIGNED_OUT_PING_KEY) pings.push(v);
      return real.call(this, k, v);
    });
    const env = mount({ startAs: A });
    await screen.findByTestId("child");
    await signOut(env);
    expect(pings).toHaveLength(1);
    expect(pings[0]).not.toContain(A.id);
  });
});

describe("the outbox sync is tied to the live session (D14)", () => {
  it("a sync running across sign-out and the next sign-in uploads nothing as B", async () => {
    const net = makeNet();
    const env = mount({ net, startAs: A });
    await screen.findByTestId("child");
    const first = await addTextUrlOutboxItem({ ownerUserId: A.id, kind: "text", text: "one" });
    const second = await addTextUrlOutboxItem({ ownerUserId: A.id, kind: "text", text: "two" });

    let releasePost!: () => void;
    const held = new Promise<void>((resolve) => {
      releasePost = resolve;
    });
    net.other = async (c) => {
      if (c.path === "/api/v1/captures") {
        await held;
        return jsonResponse(201, { capture_id: "srv-1", status: "pending" });
      }
      return jsonResponse(404, {});
    };

    const syncing = syncOutbox(env.client, A.id);
    await waitFor(() => expect(net.calls.filter((c) => c.path === "/api/v1/captures")).toHaveLength(1));
    await signOut(env);
    await signIn(env, "b@home.lan");
    await screen.findByTestId("child");
    releasePost();
    const result = await syncing;

    const posts = net.calls.filter((c) => c.path === "/api/v1/captures");
    expect(posts).toHaveLength(1);
    expect(posts[0]!.auth).toBe("Bearer tok-a");
    expect(result.stopped).toBe(true);
    const rows = await listOutboxItems(A.id);
    const byId = new Map(rows.map((m) => [m.local_capture_id, m]));
    expect(byId.get(second.local_capture_id)?.status).toBe("local_pending");
    // the first row's upload had already left under A's token, so it completed and was removed
    expect(byId.has(first.local_capture_id)).toBe(false);
  });
});

// LUM-897: TanStack runs a mutation's onSuccess / onError after its component unmounted and
// after the wipe cleared the mutation cache. Those callbacks hold the handle that
// `useQueryClient()` returned to the previous member, which must no longer write.
describe("a previous member's in-flight mutation cannot write into the next member's cache (LUM-897)", () => {
  const PREFS_KEY = ["me", "notification-preferences"] as const;

  interface Deferred<T> {
    promise: Promise<T>;
    resolve(v: T): void;
    reject(e: unknown): void;
  }
  function deferred<T>(): Deferred<T> {
    let resolve!: (v: T) => void;
    let reject!: (e: unknown) => void;
    const promise = new Promise<T>((res, rej) => {
      resolve = res;
      reject = rej;
    });
    return { promise, resolve, reject };
  }

  type Prefs = { quiet_hours: string };

  /** onSuccess writer, the shape of QuietHoursEditor / useWowState / the admin views. */
  function SuccessWriter({ slow, start }: { slow: Deferred<Prefs>; start: { current: (() => void) | null } }): null {
    const qc = useQueryClient();
    const m = useMutation({
      mutationFn: () => slow.promise,
      onSuccess: (r: Prefs) => {
        qc.setQueryData(PREFS_KEY, r);
      },
    });
    start.current = () => m.mutate();
    return null;
  }

  /** Optimistic update with an onError rollback, the shape of NotificationPrefsEditor. */
  function RollbackWriter({ slow, start }: { slow: Deferred<Prefs>; start: { current: (() => void) | null } }): null {
    const qc = useQueryClient();
    const m = useMutation({
      mutationFn: () => slow.promise,
      onMutate: () => {
        const previous = qc.getQueryData<Prefs>(PREFS_KEY);
        qc.setQueryData(PREFS_KEY, { quiet_hours: "A's optimistic quiet hours" });
        return { previous };
      },
      onError: (_e, _v, ctx) => {
        if (ctx?.previous) qc.setQueryData(PREFS_KEY, ctx.previous);
      },
      onSettled: () => {
        // Also a setQueriesData writer: every matching key, not only one.
        qc.setQueriesData({ queryKey: ["me"] }, (old: unknown) => old ?? { stale: "A's settled write" });
      },
    });
    start.current = () => m.mutate();
    return null;
  }

  async function aThenB(env: Env, start: { current: (() => void) | null }): Promise<void> {
    await screen.findByTestId("child");
    act(() => start.current!());
    await signOut(env);
    await signIn(env, "b@home.lan");
    expect(env.auth.current!.user?.id).toBe(B.id);
  }

  it("onSuccess: A's slow save lands after B signed in and does not reach B's cache", async () => {
    const slow = deferred<Prefs>();
    const start = { current: null as (() => void) | null };
    const env = mount({
      startAs: A,
      child: (e) => (
        <>
          <Child env={e} />
          <SuccessWriter slow={slow} start={start} />
        </>
      ),
    });
    await aThenB(env, start);

    await act(async () => {
      slow.resolve({ quiet_hours: "A's quiet hours" });
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(env.qc.current!.getQueryData(PREFS_KEY)).toBeUndefined();
  });

  it("onError rollback: A's failed save does not restore A's previous prefs into B's cache", async () => {
    const slow = deferred<Prefs>();
    const start = { current: null as (() => void) | null };
    const env = mount({
      startAs: A,
      child: (e) => (
        <>
          <Child env={e} />
          <RollbackWriter slow={slow} start={start} />
        </>
      ),
    });
    await screen.findByTestId("child");
    act(() => {
      env.qc.current!.setQueryData(PREFS_KEY, { quiet_hours: "A's saved quiet hours" });
    });
    await aThenB(env, start);
    expect(env.qc.current!.getQueryData(PREFS_KEY)).toBeUndefined();

    await act(async () => {
      slow.reject(new Error("slow server failed"));
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(env.qc.current!.getQueryData(PREFS_KEY)).toBeUndefined();
    expect(env.qc.current!.getQueryData(["me"])).toBeUndefined();
  });

  it("the real useOnboardingStatus: A finishing onboarding does not mark B's onboarding done", async () => {
    const net = makeNet();
    const patch = deferred<Response>();
    net.other = (c) => {
      if (c.path === "/api/v1/me/onboarding" && c.method === "PATCH") return patch.promise;
      if (c.path === "/api/v1/me/onboarding") return jsonResponse(200, { onboarding_completed: false });
      return jsonResponse(404, {});
    };
    const start = { current: null as (() => void) | null };
    function Onboarding(): null {
      const { client } = useAuth();
      const { completeOnboarding } = useOnboardingStatus(client);
      start.current = () => void completeOnboarding().catch(() => undefined);
      return null;
    }
    const env = mount({
      net,
      startAs: A,
      child: (e) => (
        <>
          <Child env={e} />
          <Onboarding />
        </>
      ),
    });
    await screen.findByTestId("child");
    act(() => start.current!());
    await waitFor(() => expect(net.calls.some((c) => c.method === "PATCH")).toBe(true));
    await signOut(env);
    await signIn(env, "b@home.lan");
    // B's own onboarding read, so the key is B's.
    await waitFor(() =>
      expect(env.qc.current!.getQueryData(["me", "onboarding"])).toEqual({ onboarding_completed: false }),
    );

    await act(async () => {
      patch.resolve(jsonResponse(200, { onboarding_completed: true }));
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(env.qc.current!.getQueryData(["me", "onboarding"])).toEqual({ onboarding_completed: false });
  });

  it("B's own writes still land, and survive A's stale write", async () => {
    const slow = deferred<Prefs>();
    const start = { current: null as (() => void) | null };
    const env = mount({
      startAs: A,
      child: (e) => (
        <>
          <Child env={e} />
          <SuccessWriter slow={slow} start={start} />
        </>
      ),
    });
    await aThenB(env, start);
    act(() => {
      env.qc.current!.setQueryData(PREFS_KEY, { quiet_hours: "B's quiet hours" });
    });
    await act(async () => {
      slow.resolve({ quiet_hours: "A's quiet hours" });
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(env.qc.current!.getQueryData(PREFS_KEY)).toEqual({ quiet_hours: "B's quiet hours" });
  });
});
