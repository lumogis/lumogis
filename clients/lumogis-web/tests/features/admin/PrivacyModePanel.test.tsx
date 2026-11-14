// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { AdminPrivacyModeView } from "../../../src/features/admin/AdminPrivacyModeView";
import { PrivacyModePanel } from "../../../src/features/admin/PrivacyModePanel";
import { MePrivacyModeView } from "../../../src/features/me/MePrivacyModeView";
import { jsonResponse } from "../../helpers/jsonResponse";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

// LUM-886: this suite mounts a real AuthProvider; mark the device as already reconciled so the
// first-sign-in wipe does not remove the view's own queries.
beforeEach(() => {
  seedSignedInDevice("admin1");
});

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
    expect(screen.getByLabelText(/Local only/i)).toBeTruthy();

    const user = userEvent.setup();
    await user.click(screen.getByLabelText(/Allow cloud/i));
    await user.click(screen.getByRole("button", { name: "Save privacy settings" }));

    await waitFor(() => expect(bodies.length).toBe(1));
    expect(bodies[0]).toMatchObject({ privacy_mode: "allow_cloud" });
  });

  it("a successful save invalidates the caller's own privacy query (LUM-881)", async () => {
    // The model-picker hint reads ["me","privacy-mode"]; without this its wording
    // would stay on the old policy until the next full page load.
    let meCalls = 0;
    const fetchImpl = vi.fn(async (input, init) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/me/privacy-mode")) {
        meCalls += 1;
        return jsonResponse(200, {
          instance: { privacy_mode: "local_only", privacy_mode_locked: false, privacy_effective: "local_only" },
          user_restriction: "inherit",
          privacy_effective: "local_only",
          can_allow_cloud: false,
        });
      }
      if (u.includes("/settings") && init?.method === "PUT") {
        return jsonResponse(200, {
          privacy_mode: "allow_cloud",
          privacy_mode_locked: false,
          privacy_effective: "allow_cloud",
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
        <PrivacyModePanel
          initial={{ privacy_mode: "local_only", privacy_mode_locked: false, privacy_effective: "local_only" }}
        />
      </AuthProvider>,
    );
    await waitFor(() => expect(meCalls).toBe(1));

    const user = userEvent.setup();
    await user.click(screen.getByLabelText(/Allow cloud/i));
    await user.click(screen.getByRole("button", { name: "Save privacy settings" }));

    await waitFor(() => expect(meCalls).toBe(2));
  });

  // The accessible-name assertion would also pass with `sr-only`; it guards
  // against "fixing" the duplicate text by deleting the legend, which would
  // drop the group's name. The class assertion pins the binding. Whether the
  // CSS actually hides the legend needs a browser: admin_search_settings.spec.ts proves it
  // for the identical `lumogis-visually-hidden` legend there; this panel has no e2e spec.
  it("names the privacy-mode radio group with a visually-hidden legend (LUM-852)", async () => {
    const fetchImpl = vi.fn(async (input) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
    globalThis.fetch = fetchImpl;

    renderPanel(fetchImpl);

    const group = await screen.findByRole("group", { name: "Household privacy mode" });
    expect(group.querySelector("legend")).toHaveClass("lumogis-visually-hidden");
  });
});

describe("AdminPrivacyModeView", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("titles the page once, not again inside the panel", async () => {
    const fetchImpl = vi.fn(async (input) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/settings")) {
        return jsonResponse(200, {
          privacy_mode: "local_only",
          privacy_mode_locked: false,
          privacy_effective: "local_only",
        });
      }
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
    globalThis.fetch = fetchImpl;

    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl });
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <AdminPrivacyModeView />
      </AuthProvider>,
    );

    expect(await screen.findByRole("heading", { level: 1, name: "Privacy mode" })).toBeTruthy();
    expect(screen.getAllByRole("heading", { name: "Privacy mode" })).toHaveLength(1);
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
