// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-853 Part A — the account menu in single-user mode (no synthetic email, no Sign out).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { ApiClient } from "../../src/api/client";
import { AccessTokenStore } from "../../src/api/tokens";
import { AuthProvider } from "../../src/auth/AuthProvider";
import { __resetClientStateBoundaryForTests } from "../../src/auth/clientStateBoundary";
import { ProfileMenuButton } from "../../src/components/ProfileMenu";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function mount(refresh: () => Response, me: () => Response) {
  const fetchImpl = (async (input: RequestInfo | URL) => {
    const path = new URL(String(input), "http://x").pathname;
    if (path === "/api/v1/auth/refresh") return refresh();
    if (path === "/api/v1/auth/me") return me();
    return jsonResponse(404, {});
  }) as typeof fetch;
  const tokens = new AccessTokenStore();
  const client = new ApiClient({ tokens, fetchImpl });
  render(
    <MemoryRouter>
      <AuthProvider client={client} tokens={tokens}>
        <ProfileMenuButton />
      </AuthProvider>
    </MemoryRouter>,
  );
}

const refreshOk = (user: { id: string; email: string; role: string }) => () =>
  jsonResponse(200, { access_token: "ax-1", token_type: "bearer", expires_in: 900, user });

describe("ProfileMenuButton", () => {
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

  it("C3 single-user mode: labelled 'Single-user mode', no synthetic email, no Sign out", async () => {
    mount(
      () => jsonResponse(503, { detail: "refresh is disabled in single-user dev mode" }),
      () => jsonResponse(200, { id: "default", email: "dev@local.lan", role: "admin" }),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Account menu" }));
    expect(screen.getByText("Single-user mode")).toBeTruthy();
    expect(screen.queryByText("dev@local.lan")).toBeNull();
    expect(screen.getByRole("menuitem", { name: "Profile" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Settings" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Admin" })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "Sign out" })).toBeNull();
  });

  it("C3b a signed-in admin sees the email and Sign out", async () => {
    const admin = { id: "a1", email: "admin@home.lan", role: "admin" };
    mount(refreshOk(admin), () => jsonResponse(200, admin));
    await userEvent.click(await screen.findByRole("button", { name: "Account menu" }));
    expect(screen.getByText("admin@home.lan")).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Sign out" })).toBeTruthy();
  });

  it("C4 member regression: role user has no Admin item and keeps Sign out", async () => {
    const alice = { id: "u1", email: "alice@home.lan", role: "user" };
    mount(refreshOk(alice), () => jsonResponse(200, alice));
    await userEvent.click(await screen.findByRole("button", { name: "Account menu" }));
    expect(screen.queryByRole("menuitem", { name: "Admin" })).toBeNull();
    expect(screen.getByRole("menuitem", { name: "Sign out" })).toBeTruthy();
  });
});
