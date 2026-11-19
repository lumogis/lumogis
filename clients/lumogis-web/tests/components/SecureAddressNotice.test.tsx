// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-331 — the shell notice appears only after this device opened the secure
// address, and never on an https page or from outside the home network.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../src/api/client";
import { AccessTokenStore } from "../../src/api/tokens";
import { AuthProvider } from "../../src/auth/AuthProvider";
import {
  SECURE_ADDRESS_NOTICE_DISMISS_KEY,
  SecureAddressNotice,
} from "../../src/components/SecureAddressNotice";
import { jsonResponse } from "../helpers/jsonResponse";
import { seedSignedInDevice } from "../helpers/signedInDevice";

const member = { id: "u1", email: "alice@home.lan", role: "user" as const };
const URL_ = "https://kq7f3r2abcdefghijklmnopqrs.homes.lumogis.app";
const HOME = { protocol: "http:", hostname: "192.168.1.20" };

function serve(publicUrl: string | null, reachable: boolean) {
  const probes: string[] = [];
  const seen: string[] = [];
  const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
    const u = String(input);
    seen.push(u);
    if (u.startsWith(URL_)) {
      probes.push(u);
      if (!reachable) throw new TypeError("Failed to fetch");
      return new Response(null, { status: 200 });
    }
    const path = new URL(u, "http://localhost").pathname;
    if (path === "/api/v1/auth/me") return jsonResponse(200, member);
    if (path === "/api/v1/secure-address") return jsonResponse(200, { url: publicUrl });
    return jsonResponse(404, {});
  }) as unknown as typeof fetch;
  globalThis.fetch = fetchImpl;
  return { fetchImpl, probes, seen };
}

function mount(fetchImpl: typeof fetch, loc: { protocol: string; hostname: string }) {
  const tokens = new AccessTokenStore();
  const client = new ApiClient({ tokens, fetchImpl });
  return render(
    <MemoryRouter>
      <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
        <p>page</p>
        <SecureAddressNotice loc={loc} />
      </AuthProvider>
    </MemoryRouter>,
  );
}

const NOTICE = { name: "Secure address" } as const;

describe("SecureAddressNotice", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    seedSignedInDevice("u1");
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("shows after the check passes, and links to the address", async () => {
    const { fetchImpl, probes } = serve(URL_, true);
    mount(fetchImpl, HOME);
    const notice = await screen.findByRole("complementary", NOTICE);
    expect(notice.textContent).toMatch(/sign in again at the new address/);
    expect(screen.getByRole("link", { name: `Open ${URL_}` }).getAttribute("href")).toBe(URL_);
    expect(probes).toEqual([`${URL_}/api/v1/health`]);
  });

  it("stays hidden when this device cannot open the address", async () => {
    const { fetchImpl, probes } = serve(URL_, false);
    mount(fetchImpl, HOME);
    await waitFor(() => expect(probes).toHaveLength(1));
    expect(screen.queryByRole("complementary", NOTICE)).toBeNull();
  });

  it("stays hidden when there is no secure address", async () => {
    const { fetchImpl, probes, seen } = serve(null, true);
    mount(fetchImpl, HOME);
    await waitFor(() => expect(seen.some((u) => u.endsWith("/api/v1/secure-address"))).toBe(true));
    await new Promise((r) => setTimeout(r, 20));
    expect(probes).toHaveLength(0);
    expect(screen.queryByRole("complementary", NOTICE)).toBeNull();
  });

  it.each([
    // A home-network hostname over https isolates the secure-origin gate.
    ["an https page", { protocol: "https:", hostname: "192.168.1.20" }],
    ["the secure address itself", { protocol: "https:", hostname: "kq7f3r2abcdefghijklmnopqrs.homes.lumogis.app" }],
    ["a page opened from outside the home network", { protocol: "http:", hostname: "lumogis.tailnet.ts.net" }],
  ])("never runs from %s", async (_label, loc) => {
    const { fetchImpl, probes, seen } = serve(URL_, true);
    mount(fetchImpl, loc);
    await waitFor(() => expect(seen.some((u) => u.endsWith("/api/v1/auth/me"))).toBe(true));
    await new Promise((r) => setTimeout(r, 50));
    expect(probes).toHaveLength(0);
    expect(seen.some((u) => u.endsWith("/api/v1/secure-address"))).toBe(false);
    expect(screen.queryByRole("complementary", NOTICE)).toBeNull();
  });

  it("Dismiss hides it and is remembered for that address", async () => {
    const { fetchImpl } = serve(URL_, true);
    const view = mount(fetchImpl, HOME);
    await screen.findByRole("complementary", NOTICE);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("complementary", NOTICE)).toBeNull();
    expect(localStorage.getItem(SECURE_ADDRESS_NOTICE_DISMISS_KEY)).toBe(URL_);
    view.unmount();

    const again = serve(URL_, true);
    mount(again.fetchImpl, HOME);
    await waitFor(() =>
      expect(again.seen.some((u) => u.endsWith("/api/v1/secure-address"))).toBe(true),
    );
    await new Promise((r) => setTimeout(r, 20));
    expect(again.probes).toHaveLength(0);
  });
});
