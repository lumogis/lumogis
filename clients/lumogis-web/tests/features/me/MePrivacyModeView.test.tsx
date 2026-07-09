// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { MePrivacyModeView } from "../../../src/features/me/MePrivacyModeView";
import { jsonResponse } from "../../helpers/jsonResponse";

describe("MePrivacyModeView", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("shows further-restrict toggle when household allows cloud", async () => {
    const fetchImpl = vi.fn(async (input, init) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, { id: "u1", role: "user" });
      if (u.includes("/api/v1/me/privacy-mode") && (!init?.method || init.method === "GET")) {
        return jsonResponse(200, {
          instance: {
            privacy_mode: "allow_cloud",
            privacy_mode_locked: false,
            privacy_effective: "allow_cloud",
          },
          user_restriction: "inherit",
          privacy_effective: "allow_cloud",
          can_allow_cloud: true,
        });
      }
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
    globalThis.fetch = fetchImpl;

    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl });
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MePrivacyModeView />
      </AuthProvider>,
    );

    expect(await screen.findByText(/Further restrict to local-only/i)).toBeTruthy();
  });
});
