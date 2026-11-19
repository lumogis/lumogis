// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-981 — first-admin boot states. The committed form of the boot harness LUM-853 used
// (real AuthProvider + RequireAuth, routed by pathname, globalThis.fetch stubbed), now inside a
// MemoryRouter because RequireAuth's <Navigate to="/setup"> throws outside a router.
//
// The server half of the signal is pinned in orchestrator/tests/test_setup_routes.py.
// What this file does not see: the real /setup page (tests/features/setup/SetupPage.test.tsx)
// and a real browser (tests/e2e/setup_first_admin.spec.ts).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { ApiClient } from "../../src/api/client";
import { AccessTokenStore } from "../../src/api/tokens";
import {
  AuthProvider,
  RequireAuth,
  SIGN_IN_OFF_LOGIN_COPY,
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

const REFRESH_401 = () => jsonResponse(401, { detail: "missing refresh cookie" });
const STATUS = (needs: boolean) => () => jsonResponse(200, { needs_setup: needs });
const ME_USER = { id: "u1", email: "alice@home.lan", role: "user" as const };

interface Call {
  path: string;
  method: string;
}

interface Script {
  refresh: (init?: RequestInit) => Response | Promise<Response>;
  status: (init?: RequestInit) => Response | Promise<Response>;
  me?: () => Response | Promise<Response>;
  login?: () => Response | Promise<Response>;
}

interface Booted {
  calls: Call[];
  script: Script;
}

function Probe(): JSX.Element {
  const user = useUser();
  // @ts-expect-error LUM-981 removed single-user mode: `singleUser` is not part of AuthValue.
  const removed = useAuth().singleUser;
  return (
    <div data-testid="probe">
      <span data-testid="who">{user ? `${user.email}|${user.role}` : "anonymous"}</span>
      <span data-testid="removed">{String(removed)}</span>
    </div>
  );
}

/** The /setup route stub: shows SETUP and a button the way the real page leaves on a 409. */
function SetupStub(): JSX.Element {
  const { leaveSetup, needsSetup } = useAuth();
  const navigate = useNavigate();
  return (
    <div>
      <span>SETUP</span>
      <span data-testid="needs-setup">{String(needsSetup)}</span>
      <button
        type="button"
        onClick={() => {
          leaveSetup();
          navigate("/");
        }}
      >
        leave setup
      </button>
    </div>
  );
}

function PathProbe(): JSX.Element {
  return <span data-testid="path">{useLocation().pathname}</span>;
}

function makeNet(script: Script): { calls: Call[]; fetchImpl: typeof fetch } {
  const calls: Call[] = [];
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), "http://x").pathname;
    calls.push({ path, method: init?.method ?? "GET" });
    if (path === "/api/v1/auth/refresh") return script.refresh(init);
    if (path === "/api/v1/setup/status") return script.status(init);
    if (path === "/api/v1/auth/me") return (script.me ?? (() => jsonResponse(200, ME_USER)))();
    if (path === "/api/v1/auth/login") {
      return (script.login ?? (() => jsonResponse(401, { detail: "invalid credentials" })))();
    }
    return jsonResponse(404, { detail: "unexpected" });
  }) as typeof fetch;
  return { calls, fetchImpl };
}

function boot(script: Script): Booted {
  const { calls, fetchImpl } = makeNet(script);
  // getSetupStatus(), login() and logout() use globalThis.fetch, not the client's fetchImpl.
  globalThis.fetch = fetchImpl;
  const tokens = new AccessTokenStore();
  const client = new ApiClient({ tokens, fetchImpl });
  render(
    <MemoryRouter initialEntries={["/"]}>
      <AuthProvider client={client} tokens={tokens}>
        <PathProbe />
        <Routes>
          <Route path="/setup" element={<SetupStub />} />
          <Route
            path="/*"
            element={
              <RequireAuth>
                <Probe />
              </RequireAuth>
            }
          />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
  return { calls, script };
}

const STARTING_TEXT = /Lumogis is still starting/;

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
const names = (calls: Call[]) => calls.map((c) => `${c.method} ${c.path}`);

describe("setup boot (LUM-981)", () => {
  it("W1 a visitor with no session on an unclaimed install is sent to /setup", async () => {
    const { calls } = boot({ refresh: REFRESH_401, status: STATUS(true) });
    await screen.findByText("SETUP");
    expect(screen.getByTestId("path").textContent).toBe("/setup");
    expect(screen.getByTestId("needs-setup").textContent).toBe("true");
    expect(names(calls)).toEqual(["POST /api/v1/auth/refresh", "GET /api/v1/setup/status"]);
  });

  it("W2 a claimed install shows the sign-in form", async () => {
    boot({ refresh: REFRESH_401, status: STATUS(false) });
    await signInHeading();
    expect(screen.queryByText("SETUP")).toBeNull();
    expect(screen.getByTestId("path").textContent).toBe("/");
  });

  it.each<[string, Script["status"]]>([
    ["HTTP 500", () => jsonResponse(500, { detail: "boom" })],
    ["HTTP 502", () => jsonResponse(502, { detail: "bad gateway" })],
    ["HTTP 503", () => jsonResponse(503, { detail: "database_unavailable" })],
    ["a network error", () => Promise.reject(new TypeError("network"))],
    ["a body that is not the contract", () => jsonResponse(200, { nope: true })],
  ])("W3 an unreachable Core (%s) shows 'still starting', never the form or the setup page", async (_n, status) => {
    boot({ refresh: REFRESH_401, status });
    await screen.findByText(STARTING_TEXT);
    expect(screen.getByRole("status").textContent).toMatch(STARTING_TEXT);
    expect(screen.queryByRole("heading", { name: "Sign in" })).toBeNull();
    expect(screen.queryByText("SETUP")).toBeNull();
  });

  it("W3 a status call that hangs for 5 s counts as unreachable", async () => {
    vi.useFakeTimers();
    const hung = (init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      });
    boot({ refresh: REFRESH_401, status: hung });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.getByText(STARTING_TEXT)).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Sign in" })).toBeNull();
    expect(screen.queryByText("SETUP")).toBeNull();
  });

  it("W3 a 404 (an older Core without the route) shows the sign-in form", async () => {
    boot({ refresh: REFRESH_401, status: () => jsonResponse(404, { detail: "Not Found" }) });
    await signInHeading();
    expect(screen.queryByText(STARTING_TEXT)).toBeNull();
  });

  it("W0 still starting: the next poll that answers 'needed' redirects to /setup", async () => {
    vi.useFakeTimers();
    const answers = [
      () => jsonResponse(503, { detail: "starting" }),
      () => jsonResponse(200, { needs_setup: true }),
    ];
    boot({ refresh: REFRESH_401, status: () => (answers.shift() ?? answers[0] ?? STATUS(true))() });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText(STARTING_TEXT)).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.getByText("SETUP")).toBeTruthy();
    expect(screen.getByTestId("path").textContent).toBe("/setup");
  });

  it("W0 still starting: the next poll that answers 'not needed' shows the sign-in form", async () => {
    vi.useFakeTimers();
    const answers = [
      () => Promise.reject(new TypeError("network")),
      () => jsonResponse(200, { needs_setup: false }),
    ];
    boot({
      refresh: REFRESH_401,
      status: () => (answers.shift() ?? (() => jsonResponse(200, { needs_setup: false })))(),
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText(STARTING_TEXT)).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeTruthy();
    expect(screen.queryByText("SETUP")).toBeNull();
  });

  it("W0 still starting: a session found by a later poll signs the visitor in", async () => {
    vi.useFakeTimers();
    let refreshes = 0;
    boot({
      refresh: () => {
        refreshes += 1;
        return refreshes === 1
          ? REFRESH_401()
          : jsonResponse(200, { access_token: "ax-1", token_type: "bearer", expires_in: 900, user: ME_USER });
      },
      status: () => jsonResponse(503, { detail: "starting" }),
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText(STARTING_TEXT)).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    for (let i = 0; i < 10 && !screen.queryByTestId("who"); i += 1) {
      // the sign-in reconcile (me query, storage) needs a few more ticks
      await act(async () => {
        await vi.advanceTimersByTimeAsync(100);
      });
    }
    expect(screen.getByTestId("who").textContent).toBe("alice@home.lan|user");
  });

  it("W7 leaveSetup() from /setup lands on the sign-in form, not back on /setup", async () => {
    boot({ refresh: REFRESH_401, status: STATUS(true) });
    await screen.findByText("SETUP");
    await userEvent.setup().click(screen.getByRole("button", { name: "leave setup" }));
    await signInHeading();
    expect(screen.queryByText("SETUP")).toBeNull();
    expect(screen.getByTestId("path").textContent).toBe("/");
  });

  it("W4 a valid session signs in without asking the setup question", async () => {
    const { calls } = boot({
      refresh: () =>
        jsonResponse(200, { access_token: "ax-1", token_type: "bearer", expires_in: 900, user: ME_USER }),
      status: STATUS(true),
    });
    await waitFor(() => expect(screen.getByTestId("who").textContent).toBe("alice@home.lan|user"));
    expect(calls.some((c) => c.path === "/api/v1/setup/status")).toBe(false);
  });

  it("W5 sign-in off: the form, the new login copy, and no banner anywhere", async () => {
    boot({
      refresh: () => jsonResponse(503, { detail: "refresh is disabled in single-user dev mode" }),
      status: STATUS(false),
      login: () => jsonResponse(503, { detail: "login is disabled in single-user dev mode" }),
    });
    await signInHeading();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/email/i), "a@home.lan");
    await user.type(screen.getByLabelText(/password/i), "correct-horse-battery");
    await user.click(screen.getByRole("button", { name: /sign in/i }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(SIGN_IN_OFF_LOGIN_COPY);
    expect(screen.queryByText(/sign-in is off/i)).toBeNull();
    expect(screen.queryByTestId("lumogis-single-user-banner")).toBeNull();
  });

  it("W6 useAuth() has no singleUser key", async () => {
    boot({
      refresh: () =>
        jsonResponse(200, { access_token: "ax-1", token_type: "bearer", expires_in: 900, user: ME_USER }),
      status: STATUS(false),
    });
    await waitFor(() => expect(screen.getByTestId("who").textContent).toBe("alice@home.lan|user"));
    expect(screen.getByTestId("removed").textContent).toBe("undefined");
  });
});
