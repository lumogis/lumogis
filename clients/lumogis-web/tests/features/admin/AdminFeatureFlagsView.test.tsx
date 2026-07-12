// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { AdminFeatureFlagsView } from "../../../src/features/admin/AdminFeatureFlagsView";
import { jsonResponse } from "../../helpers/jsonResponse";

const adminUser = { id: "admin1", email: "a@home.lan", role: "admin" as const };

// AuthProvider supplies the QueryClient (retry: false), matching the app.
function renderView(fetchImpl: typeof fetch) {
  const store = new AccessTokenStore();
  const client = new ApiClient({ tokens: store, fetchImpl });
  render(
    <AuthProvider client={client} tokens={store} skipRefreshOnMount>
      <AdminFeatureFlagsView />
    </AuthProvider>,
  );
}

describe("AdminFeatureFlagsView", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("renders flag rows with state and the egress-guard honesty note", async () => {
    const fetchImpl = vi.fn(async (input, init) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/feature-flags") && (!init?.method || init.method === "GET")) {
        return jsonResponse(200, {
          total: 2,
          enabled: 1,
          flags: [
            {
              key: "EGRESS_GUARD",
              env_var: "LUMOGIS_FF_EGRESS_GUARD",
              description: "In-process egress allowlist on LLM adapter calls (LUM-553).",
              default: false,
              enabled: true,
            },
            {
              key: "PROACTIVE_PIPES",
              env_var: "LUMOGIS_FF_PROACTIVE_PIPES",
              description: "Proactive routine engine (LUM-110).",
              default: false,
              enabled: false,
            },
          ],
        });
      }
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
    globalThis.fetch = fetchImpl;

    renderView(fetchImpl);

    expect(await screen.findByText("EGRESS_GUARD")).toBeTruthy();
    expect(screen.getByText("PROACTIVE_PIPES")).toBeTruthy();
    expect(screen.getByText("enabled")).toBeTruthy();
    expect(screen.getByText("disabled")).toBeTruthy();
    expect(screen.getByText("1 of 2 flags currently enabled.")).toBeTruthy();
    // Egress-guard honesty copy: bypassable, routing policy primary.
    expect(screen.getByText(/routing policy at the orchestrator remains the primary/i)).toBeTruthy();
  });

  it("shows an admin-only message on 403", async () => {
    const fetchImpl = vi.fn(async (input) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/feature-flags")) {
        return jsonResponse(403, { error: "forbidden", detail: "admin required" });
      }
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
    globalThis.fetch = fetchImpl;

    renderView(fetchImpl);

    expect(await screen.findByText(/Admin role required for feature flags/i)).toBeTruthy();
  });
});
