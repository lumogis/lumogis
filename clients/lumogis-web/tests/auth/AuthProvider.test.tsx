// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Vitest unit — AuthProvider behaviour: refresh-on-mount, login flow,
// onAuthExpired wiring. Parent plan §"Phase 1 Pass 1.1 item 4".

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../src/api/client";
import { AccessTokenStore } from "../../src/api/tokens";
import { AuthProvider, RequireAuth, useUser } from "../../src/auth/AuthProvider";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function loginResponse(user = { id: "u1", email: "alice@home.lan", role: "user" as const }) {
  return jsonResponse(200, {
    access_token: "ax-1",
    token_type: "bearer",
    expires_in: 900,
    user,
  });
}

function MeProbe(): JSX.Element {
  const user = useUser();
  return (
    <div>
      <span data-testid="me">{user ? `${user.email}|${user.role}` : "anonymous"}</span>
    </div>
  );
}

let originalFetch: typeof fetch;
beforeEach(() => {
  originalFetch = globalThis.fetch;
});
afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("AuthProvider — refresh-on-mount", () => {
  it("renders the login form when refresh-on-mount returns 401", async () => {
    const tokens = new AccessTokenStore();
    const fetchImpl = vi.fn(async () => jsonResponse(401, { detail: "missing refresh cookie" }));
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={tokens}>
        <RequireAuth>
          <MeProbe />
        </RequireAuth>
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
    });
  });

  it("renders children when refresh-on-mount succeeds and /me returns the user", async () => {
    const tokens = new AccessTokenStore();
    let call = 0;
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      call += 1;
      const url = String(input);
      if (url.includes("/auth/refresh")) return loginResponse();
      if (url.includes("/auth/me"))
        return jsonResponse(200, { id: "u1", email: "alice@home.lan", role: "user" });
      throw new Error(`unexpected fetch in refresh-on-mount test: ${url} (call ${call})`);
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={tokens}>
        <RequireAuth>
          <MeProbe />
        </RequireAuth>
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("me").textContent).toBe("alice@home.lan|user");
    });
    expect(tokens.get()).toBe("ax-1");
  });
});

describe("AuthProvider — login flow", () => {
  it("submits POST /api/v1/auth/login and flips to authenticated on 200", async () => {
    const tokens = new AccessTokenStore();
    const refreshFetchImpl = vi.fn(async () =>
      jsonResponse(401, { detail: "missing refresh cookie" }),
    );
    const client = new ApiClient({
      tokens,
      fetchImpl: refreshFetchImpl as unknown as typeof fetch,
    });

    // The login form uses bare `fetch()` (not the apiClient) so we mock the
    // global. The window-level fetch covers BOTH the login POST and the
    // global usage paths inside the form.
    const loginGlobal = vi.fn(async (_input: RequestInfo, init?: RequestInit) => {
      expect(init?.method).toBe("POST");
      return loginResponse();
    });
    globalThis.fetch = loginGlobal as unknown as typeof fetch;

    render(
      <AuthProvider client={client} tokens={tokens}>
        <RequireAuth>
          <MeProbe />
        </RequireAuth>
      </AuthProvider>,
    );

    await waitFor(() => screen.getByLabelText(/email/i));

    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/email/i), "alice@home.lan");
    await user.type(screen.getByLabelText(/password/i), "correct-horse-battery");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.getByTestId("me").textContent).toBe("alice@home.lan|user");
    });
    expect(tokens.get()).toBe("ax-1");
  });

  it("renders inline error and stays on login form when login returns 401 invalid credentials", async () => {
    const tokens = new AccessTokenStore();
    const refreshFetchImpl = vi.fn(async () =>
      jsonResponse(401, { detail: "missing refresh cookie" }),
    );
    const client = new ApiClient({
      tokens,
      fetchImpl: refreshFetchImpl as unknown as typeof fetch,
    });

    const loginGlobal = vi.fn(async () => jsonResponse(401, { detail: "invalid credentials" }));
    globalThis.fetch = loginGlobal as unknown as typeof fetch;

    render(
      <AuthProvider client={client} tokens={tokens}>
        <RequireAuth>
          <MeProbe />
        </RequireAuth>
      </AuthProvider>,
    );

    await waitFor(() => screen.getByLabelText(/email/i));

    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/email/i), "alice@home.lan");
    await user.type(screen.getByLabelText(/password/i), "definitely-wrong-pass");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/email or password is incorrect/i);
    });
    expect(tokens.get()).toBeNull();
  });
});

describe("AuthProvider — onAuthExpired wiring", () => {
  it("flips back to anonymous when ApiClient signals onAuthExpired", async () => {
    const tokens = new AccessTokenStore();
    let call = 0;
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      call += 1;
      const url = String(input);
      if (url.includes("/auth/refresh")) return loginResponse(); // mount succeeds
      if (url.includes("/auth/me"))
        return jsonResponse(200, { id: "u1", email: "alice@home.lan", role: "user" });
      throw new Error(`unexpected fetch ${url} call ${call}`);
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={tokens}>
        <RequireAuth>
          <MeProbe />
        </RequireAuth>
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("me").textContent).toBe("alice@home.lan|user");
    });

    // Simulate a hard 401 elsewhere in the app: client clears + onAuthExpired
    // fires, AuthProvider must flip to anonymous and re-render the login form.
    let exhaustedRefresh = false;
    fetchImpl.mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/api/v1/audit") && !exhaustedRefresh) return jsonResponse(401, {});
      if (url.includes("/auth/refresh")) {
        exhaustedRefresh = true;
        return jsonResponse(401, { detail: "missing refresh cookie" });
      }
      return jsonResponse(401, {});
    });
    await client.fetch("/api/v1/audit");

    await waitFor(() => {
      expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    });
    expect(tokens.get()).toBeNull();
  });
});
