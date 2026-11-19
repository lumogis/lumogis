// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { MePrivacyModeView } from "../../../src/features/me/MePrivacyModeView";
import { jsonResponse } from "../../helpers/jsonResponse";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

describe("MePrivacyModeView", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    seedSignedInDevice("u1");
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

    expect(await screen.findByRole("switch", { name: /Keep my AI on this machine/i })).toBeTruthy();
    expect(screen.getAllByText("Online AI allowed")).toHaveLength(2);
  });

  function mount(policy: { mode: string; locked?: boolean }, onPatch?: (b: unknown) => void, patchStatus = 200) {
    const fetchImpl = vi.fn(async (input, init) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, { id: "u1", role: "user" });
      if (u.includes("/api/v1/me/privacy-mode") && init?.method === "PATCH") {
        onPatch?.(JSON.parse(String(init.body)));
        return patchStatus === 200 ? jsonResponse(200, {}) : jsonResponse(patchStatus, { detail: "not allowed" });
      }
      if (u.includes("/api/v1/me/privacy-mode")) {
        return jsonResponse(200, {
          instance: { privacy_mode: policy.mode, privacy_mode_locked: Boolean(policy.locked), privacy_effective: policy.mode },
          user_restriction: "inherit",
          privacy_effective: policy.mode,
          can_allow_cloud: policy.mode === "allow_cloud",
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
  }

  it("turning the switch on patches local_only", async () => {
    const seen: unknown[] = [];
    mount({ mode: "allow_cloud" }, (b) => seen.push(b));
    await userEvent.click(await screen.findByRole("switch", { name: /Keep my AI on this machine/i }));
    await waitFor(() => expect(seen).toEqual([{ user_restriction: "local_only" }]));
  });

  it("shows a failed change inline", async () => {
    mount({ mode: "allow_cloud" }, undefined, 403);
    await userEvent.click(await screen.findByRole("switch", { name: /Keep my AI on this machine/i }));
    expect(await screen.findByText("not allowed")).toBeInTheDocument();
  });

  it("explains a locked household in words and offers no switch", async () => {
    mount({ mode: "local_only", locked: true });
    expect(await screen.findByText(/locked Lumogis to local AI only/i)).toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });

  it("offers a retry when loading fails", async () => {
    const fetchImpl = vi.fn(async (input) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, { id: "u1", role: "user" });
      return jsonResponse(500, { detail: "boom" });
    }) as unknown as typeof fetch;
    globalThis.fetch = fetchImpl;
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl });
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MePrivacyModeView />
      </AuthProvider>,
    );
    expect(await screen.findByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
