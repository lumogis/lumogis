// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-853 Part A — the sign-in-is-off banner and where the shell shows it.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { ApiClient } from "../../src/api/client";
import { AccessTokenStore } from "../../src/api/tokens";
import { AuthProvider } from "../../src/auth/AuthProvider";
import { __resetClientStateBoundaryForTests } from "../../src/auth/clientStateBoundary";
import { AppShell } from "../../src/components/AppShell";
import {
  REMOTE_ACCESS_SAFETY_DOC_URL,
  SINGLE_USER_BANNER_COPY,
  SingleUserBanner,
  TURN_ON_SIGN_IN_DOC_URL,
} from "../../src/components/SingleUserBanner";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("SingleUserBanner", () => {
  it("C1 renders the notice and both links", () => {
    render(<SingleUserBanner visible />);
    const banner = screen.getByRole("complementary", { name: "Sign-in is off" });
    expect(banner.textContent).toContain(SINGLE_USER_BANNER_COPY);
    expect(banner.textContent).toMatch(/internet/);
    expect(banner.textContent).not.toMatch(/Lumogis Server|sharing toggle|apps\//i);
    const turnOn = screen.getByRole("link", { name: "Turn on sign-in" });
    const safe = screen.getByRole("link", { name: "Safe remote access" });
    expect(turnOn.getAttribute("href")).toBe(TURN_ON_SIGN_IN_DOC_URL);
    expect(safe.getAttribute("href")).toBe(REMOTE_ACCESS_SAFETY_DOC_URL);
    for (const a of [turnOn, safe]) {
      expect(a.getAttribute("target")).toBe("_blank");
      expect(a.getAttribute("rel")).toContain("noopener");
      expect(a.getAttribute("rel")).toContain("noreferrer");
    }
  });

  it("C1b renders nothing when not visible", () => {
    const { container } = render(<SingleUserBanner visible={false} />);
    expect(container.firstChild).toBeNull();
  });
});

describe("AppShell banner (LUM-853)", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
    localStorage.clear();
    sessionStorage.clear();
    __resetClientStateBoundaryForTests();
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  function mountShell(refresh: () => Response, me: () => Response) {
    const fetchImpl = (async (input: RequestInfo | URL) => {
      const path = new URL(String(input), "http://x").pathname;
      if (path === "/api/v1/auth/refresh") return refresh();
      if (path === "/api/v1/auth/me") return me();
      return jsonResponse(404, {});
    }) as typeof fetch;
    const tokens = new AccessTokenStore();
    const client = new ApiClient({ tokens, fetchImpl });
    return render(
      <MemoryRouter>
        <AuthProvider client={client} tokens={tokens}>
          <AppShell>
            <p>page</p>
          </AppShell>
        </AuthProvider>
      </MemoryRouter>,
    );
  }

  it("C2 shows the banner only in single-user mode", async () => {
    mountShell(
      () => jsonResponse(503, { detail: "refresh is disabled in single-user dev mode" }),
      () => jsonResponse(200, { id: "default", email: "dev@local.lan", role: "admin" }),
    );
    await screen.findByRole("complementary", { name: "Sign-in is off" });
  });

  it("C2b a signed-in member sees no banner", async () => {
    mountShell(
      () =>
        jsonResponse(200, {
          access_token: "ax-1",
          token_type: "bearer",
          expires_in: 900,
          user: { id: "u1", email: "alice@home.lan", role: "user" },
        }),
      () => jsonResponse(200, { id: "u1", email: "alice@home.lan", role: "user" }),
    );
    await screen.findByText("page");
    await waitFor(() => expect(screen.getByLabelText("Account menu")).toBeTruthy());
    expect(screen.queryByRole("complementary", { name: "Sign-in is off" })).toBeNull();
  });
});
