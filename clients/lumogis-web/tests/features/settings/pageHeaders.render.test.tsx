// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Every Settings page, mounted for real (LUM-993 Pass 3): while its data never
// arrives, and when every request fails with 500, the page still shows exactly
// one h1 with its title and its purpose line. A view that throws or blanks the
// page fails here. jsdom proves structure only; layout is proven in Playwright
// (tests/e2e/settings_nav.spec.ts). Mine/Household pages are mounted in both views.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { help } from "../../../src/copy/help";

// One stable auth value per test, as the real provider gives: a new object (or
// token store) on every call would re-run every effect that depends on it.
const state: { auth: Record<string, unknown> | null } = { auth: null };
const ADMIN = { id: "a1", email: "admin@home.lan", role: "admin" as const };
vi.mock("../../../src/auth/AuthProvider", () => ({
  useUser: () => ADMIN,
  useAuth: () => state.auth,
}));

import { SettingsLayout } from "../../../src/features/settings/SettingsLayout";
import { SettingsPage } from "../../../src/features/settings/SettingsPage";
import { SETTINGS } from "../../../src/features/settings/settingsNav";

type Mode = "pending" | "error";

function authFor(mode: Mode): Record<string, unknown> {
  const tokens = new AccessTokenStore();
  tokens.set("test-token");
  const fetchImpl = (async () =>
    mode === "pending"
      ? new Promise<Response>(() => undefined)
      : new Response(JSON.stringify({ detail: "boom" }), {
          status: 500,
          headers: { "Content-Type": "application/json" },
        })) as typeof fetch;
  const client = new ApiClient({ tokens, fetchImpl });
  return { status: "authenticated", user: ADMIN, client, tokens, needsSetup: false, starting: false, logout: async () => {} };
}

function mount(path: string, mode: Mode) {
  state.auth = authFor(mode);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/settings" element={<SettingsLayout />}>
            <Route path=":slug" element={<SettingsPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const cases: Array<[string, string, string]> = SETTINGS.flatMap((e) => {
  const page = (help[e.helpKey] as { page: string }).page;
  const base: [string, string, string] = [`/settings/${e.slug}`, e.title, page];
  return e.scope ? [base, [`/settings/${e.slug}?view=household`, e.title, page] as [string, string, string]] : [base];
});

describe.each<Mode>(["pending", "error"])("every Settings page (%s)", (mode) => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it.each(cases)("%s has one h1 and its purpose line", async (path, title, page) => {
    // Views may log their failed requests; that is expected here.
    vi.spyOn(console, "error").mockImplementation(() => {});
    mount(path, mode);
    const h1s = await screen.findAllByRole("heading", { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent(title);
    expect(screen.getByText(page)).toBeInTheDocument();
    // Let error states settle, then check again: still exactly one h1.
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });
});
