// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { PrivacyBlockedHint } from "../../../src/features/_shared/PrivacyBlockedHint";
import type { PrivacyHintSurface } from "../../../src/features/_shared/privacyBlockCopy";
import { jsonResponse } from "../../helpers/jsonResponse";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

// LUM-886: this suite mounts a real AuthProvider; mark the device as already reconciled so the
// first-sign-in wipe does not remove the view's own queries.
beforeEach(() => {
  seedSignedInDevice("u1");
});

function policy(over: { instance?: "local_only" | "allow_cloud"; locked?: boolean } = {}) {
  const instance = over.instance ?? "local_only";
  return {
    instance: { privacy_mode: instance, privacy_mode_locked: over.locked ?? false, privacy_effective: instance },
    user_restriction: "inherit",
    privacy_effective: instance,
    can_allow_cloud: instance === "allow_cloud",
  };
}

function mount(opts: {
  role: "admin" | "user";
  surface?: PrivacyHintSurface;
  privacy?: Response | (() => Response);
}) {
  const fetchImpl = vi.fn(async (input: RequestInfo) => {
    const url = String(input);
    if (url.includes("/api/v1/auth/me")) return jsonResponse(200, { id: "u1", email: "u@home.lan", role: opts.role });
    if (url.includes("/api/v1/me/privacy-mode")) {
      return typeof opts.privacy === "function" ? opts.privacy() : (opts.privacy ?? jsonResponse(200, policy()));
    }
    return jsonResponse(404, {});
  });
  const store = new AccessTokenStore();
  const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
  render(
    <MemoryRouter>
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <PrivacyBlockedHint surface={opts.surface ?? "picker"} />
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("PrivacyBlockedHint", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("(a) admin + local-only: text and a link to /admin/privacy-mode", async () => {
    mount({ role: "admin" });
    const hint = await screen.findByTestId("privacy-blocked-hint");
    expect(hint).toHaveAttribute("role", "status");
    expect(hint).toHaveTextContent("Cloud models are off because household privacy mode is local-only.");
    expect(screen.getByRole("link", { name: "Allow cloud models in Privacy mode" })).toHaveAttribute(
      "href",
      "/admin/privacy-mode",
    );
  });

  it("(b) member: 'Ask your household admin' and no link", async () => {
    mount({ role: "user" });
    const hint = await screen.findByTestId("privacy-blocked-hint");
    expect(hint).toHaveTextContent("Ask your household admin");
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("(c) allow_cloud renders nothing", async () => {
    mount({ role: "admin", privacy: jsonResponse(200, policy({ instance: "allow_cloud" })) });
    await waitFor(() => expect(screen.queryByTestId("privacy-blocked-hint")).not.toBeInTheDocument());
    // Let the query settle so "nothing" is not just "still loading".
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("privacy-blocked-hint")).not.toBeInTheDocument();
  });

  it("(d) policy fetch 500 on the picker surface renders the fallback", async () => {
    mount({ role: "admin", privacy: jsonResponse(500, { detail: "boom" }) });
    const hint = await screen.findByTestId("privacy-blocked-hint");
    expect(hint).toHaveTextContent("Cloud models are off because privacy mode is local-only.");
  });

  it("(e) policy fetch 500 on the key-save surface renders nothing", async () => {
    mount({ role: "admin", surface: "key-save", privacy: jsonResponse(500, { detail: "boom" }) });
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("privacy-blocked-hint")).not.toBeInTheDocument();
  });

  it("(f) a locked household offers 'Unlock it in Privacy mode'", async () => {
    mount({ role: "admin", privacy: jsonResponse(200, policy({ locked: true })) });
    expect(await screen.findByRole("link", { name: "Unlock it in Privacy mode" })).toBeInTheDocument();
  });
});
