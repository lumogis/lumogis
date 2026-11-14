// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-853 Part A — single-user boot. These are the committed form of the plan-review R2
// premise probe, inverted: with sign-in off, Core answers refresh and login 503 but serves
// `GET /api/v1/auth/me` as its single admin without a credential, and Lumogis Web must ask.
// The server half of the signal is pinned in orchestrator/tests/test_auth_phase1.py
// (`test_me_in_dev_mode_returns_synthesised_admin`, `test_me_without_bearer_in_true_mode_returns_401`,
// `test_me_with_refresh_cookie_but_no_bearer_in_true_mode_returns_401`).
//
// What this file does not see: query persistence and the service worker (Vitest disables
// persistence at runtime; `tests/pwa/queryPersistence.test.ts` and `serviceWorker.test.ts`
// pin them). S6 closes only the gap that `tests/auth/storageInventory.test.ts` cannot see: a
// mode key written from inside the already-inventoried AuthProvider.tsx.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";

import { ApiClient } from "../../src/api/client";
import { AccessTokenStore } from "../../src/api/tokens";
import {
  AuthProvider,
  RequireAuth,
  isSingleUserIdentity,
  useAuth,
  useUser,
} from "../../src/auth/AuthProvider";
import { __resetClientStateBoundaryForTests } from "../../src/auth/clientStateBoundary";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function loginResponse(user = { id: "u1", email: "alice@home.lan", role: "user" as const }) {
  return jsonResponse(200, { access_token: "ax-1", token_type: "bearer", expires_in: 900, user });
}

// Bodies copied from orchestrator/routes/auth.py (refresh/login with sign-in off).
const REFRESH_503 = () => jsonResponse(503, { detail: "refresh is disabled in single-user dev mode" });
const DEFAULT_ADMIN = { id: "default", email: "dev@local.lan", role: "admin" as const };
const MISSING_TOKEN_401 = () => jsonResponse(401, { error: "missing token" });

interface Call {
  path: string;
  method: string;
  auth: string | null;
  signal?: AbortSignal | null;
}

interface Script {
  refresh: () => Response | Promise<Response>;
  me: (init?: RequestInit) => Response | Promise<Response>;
}

interface Booted {
  calls: Call[];
  script: Script;
  client: ApiClient;
  tokens: AccessTokenStore;
  qc: { current: QueryClient | null };
}

function Probe(): JSX.Element {
  const user = useUser();
  const { singleUser } = useAuth();
  return (
    <div data-testid="probe">
      <span data-testid="who">{user ? `${user.email}|${user.role}` : "anonymous"}</span>
      <span data-testid="single">{String(singleUser)}</span>
    </div>
  );
}

function QcSpy({ qc }: { qc: { current: QueryClient | null } }): null {
  qc.current = useQueryClient();
  return null;
}

function Logout(): JSX.Element {
  const { logout } = useAuth();
  return (
    <button type="button" onClick={() => void logout()}>
      call logout
    </button>
  );
}

function makeNet(script: Script): { calls: Call[]; fetchImpl: typeof fetch } {
  const calls: Call[] = [];
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), "http://x").pathname;
    calls.push({
      path,
      method: init?.method ?? "GET",
      auth: new Headers(init?.headers).get("Authorization"),
      signal: init?.signal,
    });
    if (path === "/api/v1/auth/refresh") return script.refresh();
    if (path === "/api/v1/auth/me") return script.me(init);
    if (path === "/api/v1/auth/login") return jsonResponse(503, { detail: "login is disabled in single-user dev mode" });
    return jsonResponse(404, { detail: "unexpected" });
  }) as typeof fetch;
  return { calls, fetchImpl };
}

function boot(script: Script): Booted {
  const { calls, fetchImpl } = makeNet(script);
  // login() and logout() use globalThis.fetch, not the client's fetchImpl.
  globalThis.fetch = fetchImpl;
  const tokens = new AccessTokenStore();
  const client = new ApiClient({ tokens, fetchImpl });
  const qc: Booted["qc"] = { current: null };
  render(
    <AuthProvider client={client} tokens={tokens}>
      <QcSpy qc={qc} />
      <RequireAuth>
        <Probe />
        <Logout />
      </RequireAuth>
    </AuthProvider>,
  );
  return { calls, script, client, tokens, qc };
}

const authOff = (): Script => ({ refresh: REFRESH_503, me: () => jsonResponse(200, DEFAULT_ADMIN) });

let originalFetch: typeof fetch;
beforeEach(() => {
  originalFetch = globalThis.fetch;
  localStorage.clear();
  sessionStorage.clear();
  __resetClientStateBoundaryForTests();
});
afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const signInHeading = () => screen.findByRole("heading", { name: "Sign in" });

describe("single-user boot (LUM-853)", () => {
  it("S1 auth-off Core: boots into single-user mode as the server's identity", async () => {
    const { calls } = boot(authOff());
    await waitFor(() => expect(screen.getByTestId("who").textContent).toBe("dev@local.lan|admin"));
    expect(screen.getByTestId("single").textContent).toBe("true");
    expect(screen.queryByRole("heading", { name: "Sign in" })).toBeNull();
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      "POST /api/v1/auth/refresh",
      "GET /api/v1/auth/me",
    ]);
    expect(calls[1]?.auth).toBeNull();
  });

  it("S2 auth-on Core with no session: one probe, then the sign-in form, no loop", async () => {
    const { calls } = boot({ refresh: REFRESH_503, me: MISSING_TOKEN_401 });
    await signInHeading();
    await new Promise((r) => setTimeout(r, 50));
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      "POST /api/v1/auth/refresh",
      "GET /api/v1/auth/me",
    ]);
  });

  it.each([
    ["network error on both", () => Promise.reject(new TypeError("network")), () => Promise.reject(new TypeError("network"))],
    ["refresh 503, probe network error", REFRESH_503, () => Promise.reject(new TypeError("network"))],
    ["refresh 503, probe 502", REFRESH_503, () => jsonResponse(502, { detail: "bad gateway" })],
    ["refresh 503, probe 500", REFRESH_503, () => jsonResponse(500, { detail: "boom" })],
  ])("S3 Core unreachable or erroring: sign-in form (%s)", async (_name, refresh, me) => {
    boot({ refresh, me });
    await signInHeading();
    expect(screen.queryByTestId("probe")).toBeNull();
  });

  it("S4 auth-on anonymous boot keeps today's behaviour", async () => {
    const { calls } = boot({
      refresh: () => jsonResponse(401, { detail: "missing refresh cookie" }),
      me: MISSING_TOKEN_401,
    });
    await signInHeading();
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      "POST /api/v1/auth/refresh",
      "GET /api/v1/auth/me",
    ]);
  });

  it.each<[string, () => Response]>([
    ["empty object", () => jsonResponse(200, {})],
    ["empty id", () => jsonResponse(200, { id: "", email: "x", role: "admin" })],
    ["role user", () => jsonResponse(200, { ...DEFAULT_ADMIN, role: "user" })],
    ["no email", () => jsonResponse(200, { id: "default", role: "admin" })],
    ["array", () => jsonResponse(200, [])],
    ["html body", () => new Response("<html>", { status: 200, headers: { "Content-Type": "text/html" } })],
  ])("S5 malformed /auth/me body never enters single-user mode (%s)", async (_name, me) => {
    boot({ refresh: REFRESH_503, me });
    await signInHeading();
    expect(screen.queryByTestId("probe")).toBeNull();
  });

  it("S6 single-user mode is never stored", async () => {
    boot(authOff());
    await waitFor(() => expect(screen.getByTestId("single").textContent).toBe("true"));
    // Only the LUM-886 member markers (they identify the member, not the mode).
    expect(Object.keys(localStorage).sort()).toEqual(["lumogis:last-signed-in-user"]);
    expect(Object.keys(sessionStorage).sort()).toEqual(["lumogis:tab-owner"]);
    expect(localStorage.getItem("lumogis:last-signed-in-user")).toBe("default");
    expect(sessionStorage.getItem("lumogis:tab-owner")).toBe("default");
  });

  it("S7 sign-in turned on under an open tab: the next 401 signs the tab out", async () => {
    const net = boot(authOff());
    await waitFor(() => expect(screen.getByTestId("single").textContent).toBe("true"));
    const qc = net.qc.current!;
    qc.setQueryData(["lum853-seed"], 1);

    net.script.refresh = () => jsonResponse(401, { detail: "missing refresh cookie" });
    net.script.me = MISSING_TOKEN_401;
    const before = net.calls.length;
    await act(async () => {
      await qc.invalidateQueries({ queryKey: ["auth", "me"] });
    });
    await signInHeading();
    await waitFor(() => expect(qc.getQueryData(["lum853-seed"])).toBeUndefined());
    expect(screen.queryByTestId("probe")).toBeNull();
    const after = net.calls.slice(before).map((c) => `${c.method} ${c.path}`);
    expect(after).toContain("GET /api/v1/auth/me");
    expect(after).toContain("POST /api/v1/auth/refresh");
    // The provider does not re-probe after a sign-out: exactly one unauthenticated /auth/me.
    expect(after.filter((p) => p === "GET /api/v1/auth/me")).toHaveLength(1);
  });

  it("S8 logout() is a no-op in single-user mode", async () => {
    const net = boot(authOff());
    await waitFor(() => expect(screen.getByTestId("single").textContent).toBe("true"));
    await act(async () => {
      screen.getByRole("button", { name: "call logout" }).click();
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(screen.getByTestId("who").textContent).toBe("dev@local.lan|admin");
    expect(net.calls.some((c) => c.path === "/api/v1/auth/logout")).toBe(false);
    expect(localStorage.getItem("lumogis:signed-out-ping")).toBeNull();
  });

  it("S9 a signed-in member is not single-user", async () => {
    const { calls } = boot({
      refresh: () => loginResponse(),
      me: () => jsonResponse(200, { id: "u1", email: "alice@home.lan", role: "user" }),
    });
    await waitFor(() => expect(screen.getByTestId("who").textContent).toBe("alice@home.lan|user"));
    expect(screen.getByTestId("single").textContent).toBe("false");
    expect(calls.filter((c) => c.path === "/api/v1/auth/me" && c.auth === null)).toHaveLength(0);
  });

  it("S10 the mode is re-derived on every load", async () => {
    const first = render(
      <AuthProvider
        client={new ApiClient({ tokens: new AccessTokenStore(), fetchImpl: makeNet(authOff()).fetchImpl })}
        tokens={new AccessTokenStore()}
      >
        <RequireAuth>
          <Probe />
        </RequireAuth>
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("single").textContent).toBe("true"));
    first.unmount();

    // New load, sign-in now on: a new provider, client and token store; nothing carried over.
    boot({ refresh: () => jsonResponse(401, { detail: "missing refresh cookie" }), me: MISSING_TOKEN_401 });
    await signInHeading();
    expect(screen.queryByTestId("probe")).toBeNull();
  });

  it("S10b isSingleUserIdentity accepts only a well-formed admin", () => {
    expect(isSingleUserIdentity(DEFAULT_ADMIN)).toBe(true);
    for (const bad of [
      {},
      { id: "", email: "x", role: "admin" },
      { ...DEFAULT_ADMIN, role: "user" },
      { id: "default", role: "admin" },
      [],
      null,
      "x",
    ]) {
      expect(isSingleUserIdentity(bad)).toBe(false);
    }
  });

  it("S12 a hung probe times out to the sign-in form", async () => {
    vi.useFakeTimers();
    const hung = (init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      });
    const { calls } = boot({ refresh: REFRESH_503, me: hung });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeTruthy();
    const meCall = calls.find((c) => c.path === "/api/v1/auth/me");
    expect(meCall?.signal?.aborted).toBe(true);
  });

  it("S13 single-user mode survives a non-401 /me failure", async () => {
    const net = boot(authOff());
    await waitFor(() => expect(screen.getByTestId("single").textContent).toBe("true"));
    const qc = net.qc.current!;

    net.script.me = () => jsonResponse(502, { detail: "bad gateway" });
    await act(async () => {
      await qc.invalidateQueries({ queryKey: ["auth", "me"] });
      // Observers are notified on a later tick; let the provider re-render with the error.
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(qc.getQueryState(["auth", "me"])?.status).toBe("error");
    expect(screen.getByTestId("who").textContent).toBe("dev@local.lan|admin");
    expect(screen.queryByRole("heading", { name: "Sign in" })).toBeNull();

    net.script.me = () => jsonResponse(200, DEFAULT_ADMIN);
    await act(async () => {
      await qc.invalidateQueries({ queryKey: ["auth", "me"] });
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(qc.getQueryState(["auth", "me"])?.status).toBe("success");
    expect(screen.getByTestId("who").textContent).toBe("dev@local.lan|admin");
    expect(screen.getByTestId("single").textContent).toBe("true");
  });
});
