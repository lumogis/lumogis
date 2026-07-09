// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { PrivacyModePanel } from "../../../src/features/admin/PrivacyModePanel";
import { MePrivacyModeView } from "../../../src/features/me/MePrivacyModeView";
import { jsonResponse } from "../../helpers/jsonResponse";

const adminUser = { id: "admin1", email: "a@home.lan", role: "admin" as const };

function renderPanel(fetchImpl: typeof fetch) {
  const store = new AccessTokenStore();
  const client = new ApiClient({ tokens: store, fetchImpl });
  render(
    <AuthProvider client={client} tokens={store} skipRefreshOnMount>
      <PrivacyModePanel
        initial={{
          privacy_mode: "local_only",
          privacy_mode_locked: false,
          privacy_effective: "local_only",
        }}
      />
    </AuthProvider>,
  );
}

describe("PrivacyModePanel", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("renders local-only controls and saves via PUT /settings", async () => {
    const bodies: unknown[] = [];
    const fetchImpl = vi.fn(async (input, init) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/settings") && init?.method === "PUT") {
        bodies.push(JSON.parse(String(init.body)));
        return jsonResponse(200, {
          privacy_mode: "allow_cloud",
          privacy_mode_locked: false,
          privacy_effective: "allow_cloud",
        });
      }
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
    globalThis.fetch = fetchImpl;

    renderPanel(fetchImpl);
    expect(screen.getByText("Privacy mode")).toBeTruthy();

    const user = userEvent.setup();
    await user.click(screen.getByLabelText(/Allow cloud/i));
    await user.click(screen.getByRole("button", { name: "Save privacy settings" }));

    await waitFor(() => expect(bodies.length).toBe(1));
    expect(bodies[0]).toMatchObject({ privacy_mode: "allow_cloud" });
  });
});

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
