// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-981 — the "Create the admin account" page. The page talks to the public claim route
// through the global fetch (stubbed here) and to the auth context (replaced by a small store,
// so a test can flip `needsSetup` the way the real provider does after a claim). The boot half
// (status → /setup, leaving setup) is pinned with the real provider in tests/auth/setupBoot.test.tsx.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useSyncExternalStore } from "react";
import { MemoryRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";

import type { LoginResponse } from "../../../src/api/auth";
import { SIGN_IN_DOC_URL } from "../../../src/auth/docLinks";
import { SetupPage } from "../../../src/features/setup/SetupPage";

interface AuthState {
  needsSetup: boolean;
  status: "loading" | "anonymous" | "authenticated";
}

const auth = {
  state: { needsSetup: true, status: "anonymous" } as AuthState,
  listeners: new Set<() => void>(),
  adoptSession: vi.fn(),
  leaveSetup: vi.fn(),
  set(next: Partial<AuthState>): void {
    auth.state = { ...auth.state, ...next };
    auth.listeners.forEach((l) => l());
  },
};

vi.mock("../../../src/auth/AuthProvider", () => ({
  useAuth: () => {
    const state = useSyncExternalStore(
      (l) => {
        auth.listeners.add(l);
        return () => auth.listeners.delete(l);
      },
      () => auth.state,
    );
    return { ...state, adoptSession: auth.adoptSession, leaveSetup: auth.leaveSetup };
  },
}));

const LOGIN: LoginResponse = {
  access_token: "ax-1",
  token_type: "bearer",
  expires_in: 900,
  user: { id: "u1", email: "admin@example.com", role: "admin" },
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function PathProbe(): JSX.Element {
  return <span data-testid="path">{useLocation().pathname}</span>;
}

/** "/" behaves like RequireAuth: it sends a visitor back to /setup while setup is still needed. */
function Home(): JSX.Element {
  const { needsSetup } = auth.state;
  return needsSetup ? <Navigate to="/setup" replace /> : <span>SIGN IN FORM</span>;
}

function renderPage(opts: { setupAt?: string } = {}) {
  return render(
    <MemoryRouter initialEntries={["/setup"]}>
      <PathProbe />
      <Routes>
        <Route path={opts.setupAt ?? "/setup"} element={<SetupPage />} />
        <Route path="/chat" element={<span>CHAT</span>} />
        <Route path="/" element={<HomeBound />} />
      </Routes>
    </MemoryRouter>,
  );
}

function HomeBound(): JSX.Element {
  // Re-render when the store changes, like RequireAuth does through useAuth().
  useSyncExternalStore(
    (l) => {
      auth.listeners.add(l);
      return () => auth.listeners.delete(l);
    },
    () => auth.state,
  );
  return <Home />;
}

async function fill(email = "admin@example.com", password = "correct-horse-battery", confirm = password) {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Email"), email);
  await user.type(screen.getByLabelText("Password"), password);
  await user.type(screen.getByLabelText("Confirm password"), confirm);
  return user;
}

let originalFetch: typeof fetch;
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  originalFetch = globalThis.fetch;
  fetchMock = vi.fn(async () => json(200, LOGIN));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  auth.state = { needsSetup: true, status: "anonymous" };
  auth.listeners.clear();
  auth.adoptSession.mockReset();
  auth.leaveSetup.mockReset();
});
afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("SetupPage (LUM-981)", () => {
  it("S1 shows the heading, the lede and the three labelled fields", () => {
    renderPage();
    expect(screen.getByRole("heading", { name: "Create the admin account" })).toBeTruthy();
    expect(screen.getByText(/first account on this Lumogis/)).toBeTruthy();
    expect(screen.getByText(/It can only be created once/)).toBeTruthy();
    expect(screen.getByLabelText("Email")).toHaveAttribute("autocomplete", "username");
    expect(screen.getByLabelText("Password")).toHaveAttribute("autocomplete", "new-password");
    expect(screen.getByLabelText("Password")).toHaveAttribute("minlength", "12");
    expect(screen.getByLabelText("Confirm password")).toHaveAttribute("type", "password");
    expect(screen.getByText(/Addresses ending in \.local or \.test are not accepted/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Create account" })).toBeTruthy();
  });

  it("S2 two different passwords show the mismatch copy and send nothing", async () => {
    renderPage();
    const user = await fill("admin@example.com", "correct-horse-battery", "correct-horse-batterz");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect((await screen.findByRole("alert")).textContent).toBe("The two passwords do not match.");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("S3 success adopts the session and goes to /chat", async () => {
    renderPage();
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Create account" }));
    await screen.findByText("CHAT");
    expect(auth.adoptSession).toHaveBeenCalledWith(LOGIN);
    expect(screen.getByTestId("path").textContent).toBe("/chat");
    const [url, init] = fetchMock.mock.calls[0]! as [string, RequestInit];
    expect(url).toBe("/api/v1/setup/claim");
    expect(init.credentials).toBe("include");
  });

  it("S4 a 409 says it was already created, offers sign in first, and the button leaves setup", async () => {
    fetchMock.mockResolvedValue(json(409, { detail: "setup is not open" }));
    // The real leaveSetup makes the gate no_session; this stand-in makes `needsSetup` false.
    auth.leaveSetup.mockImplementation(() => auth.set({ needsSetup: false }));
    renderPage();
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText("The admin account on this Lumogis has already been created.")).toBeTruthy();
    const goToSignIn = screen.getByRole("button", { name: "Go to sign in" });
    const advice = screen.getByText(/If you didn.t create it, someone else on your network did/);
    // The sign-in action comes before the reinstall advice.
    expect(goToSignIn.compareDocumentPosition(advice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole("link", { name: "quickstart" })).toHaveAttribute("href", SIGN_IN_DOC_URL);

    await user.click(goToSignIn);
    expect(auth.leaveSetup).toHaveBeenCalledTimes(1);
    await screen.findByText("SIGN IN FORM");
    expect(screen.getByTestId("path").textContent).toBe("/");
  });

  it.each(["setup_host_not_allowed", "origin mismatch"])(
    "S4b a 403 (%s) tells the visitor to use the network address",
    async (detail) => {
      fetchMock.mockResolvedValue(json(403, { detail }));
      renderPage();
      const user = await fill();
      await user.click(screen.getByRole("button", { name: "Create account" }));
      const alert = await screen.findByRole("alert");
      expect(alert.textContent).toContain("Open Lumogis at this computer's network address");
      expect(alert.textContent).toContain("LUMOGIS_PUBLIC_ORIGIN");
      expect(screen.queryByRole("button", { name: "Go to sign in" })).toBeNull();
    },
  );

  it("S5 a string 422 detail is shown as is", async () => {
    fetchMock.mockResolvedValue(json(422, { detail: "password must be at least 12 characters" }));
    renderPage();
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect((await screen.findByRole("alert")).textContent).toBe("password must be at least 12 characters");
  });

  it("S5 an array 422 detail (Pydantic) shows the generic copy", async () => {
    fetchMock.mockResolvedValue(json(422, { detail: [{ loc: ["body", "email"], msg: "bad", type: "value_error" }] }));
    renderPage();
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Check the email address and use a password of at least 12 characters.",
    );
  });

  it("S6 a 429 asks the visitor to wait a minute", async () => {
    fetchMock.mockResolvedValue(
      json(429, { detail: "too many failed attempts; try again in a minute" }),
    );
    renderPage();
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Too many attempts. Try again in a minute.");
  });

  it("S10 a 500 shows the generic error and a Go to sign in button that leaves setup", async () => {
    fetchMock.mockResolvedValue(json(500, { detail: "boom" }));
    auth.leaveSetup.mockImplementation(() => auth.set({ needsSetup: false }));
    renderPage();
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Lumogis could not create the account. Try again.",
    );
    await user.click(screen.getByRole("button", { name: "Go to sign in" }));
    expect(auth.leaveSetup).toHaveBeenCalledTimes(1);
    await screen.findByText("SIGN IN FORM");
  });

  it("S11 a 503 says the database was unreachable and offers no sign-in (no account exists)", async () => {
    fetchMock.mockResolvedValue(json(503, { detail: "database_unavailable" }));
    renderPage();
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Lumogis could not reach its database. Try again.",
    );
    expect(screen.queryByRole("button", { name: "Go to sign in" })).toBeNull();
  });

  it("S7 when setup is not needed the visitor is sent to /", async () => {
    auth.state = { needsSetup: false, status: "anonymous" };
    renderPage();
    await screen.findByText("SIGN IN FORM");
    expect(screen.getByTestId("path").textContent).toBe("/");
  });

  it("S7 while Core is still being asked, a loading splash shows instead of a redirect", () => {
    auth.state = { needsSetup: false, status: "loading" };
    renderPage();
    expect(screen.getByRole("status").textContent).toMatch(/Loading/);
    expect(screen.getByTestId("path").textContent).toBe("/setup");
  });

  it("S9 after a claim, `needsSetup` turning false does not redirect the page to /", async () => {
    // The page is mounted at every path here, so it is still on screen after navigate("/chat"):
    // without the `done` guard its "setup is not needed" redirect would then win and land on "/".
    auth.adoptSession.mockImplementation(() => {
      act(() => auth.set({ needsSetup: false }));
    });
    renderPage({ setupAt: "*" });
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(auth.adoptSession).toHaveBeenCalled());
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(screen.getByTestId("path").textContent).toBe("/chat");
  });
});
