// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// FP-046 — MeMcpTokensView list + mint + CopyOnceModal.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { MeMcpTokensView } from "../../../src/features/me/MeMcpTokensView";
import { jsonResponse } from "../../helpers/jsonResponse";

describe("MeMcpTokensView", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("loads tokens, mints, and shows the copy-once modal with plaintext", async () => {
    const user = { id: "u1", email: "u@home.lan", role: "user" as const };
    const mcpList = [
      { id: "t1", label: "a", created_at: "2020-01-01T00:00:00Z" },
    ];
    const refreshBody = { access_token: "x", user };

    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/auth/refresh")) return jsonResponse(200, refreshBody);
      if (u.includes("/me/mcp-tokens") && init?.method === "POST")
        return jsonResponse(201, {
          plaintext: "plain-secret-123",
          token: { id: "new1", label: "l", created_at: "2020-01-02T00:00:00Z" },
        });
      if (u.includes("/me/mcp-tokens")) return jsonResponse(200, mcpList);
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });

    const userEv = userEvent.setup();
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MeMcpTokensView />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("a")).toBeInTheDocument();
    });
    expect(screen.getByText("t1")).toBeInTheDocument();

    await userEv.type(screen.getByPlaceholderText(/label/i), "my");
    await userEv.click(screen.getByRole("button", { name: /^mint$/i }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /new mcp token/i })).toBeInTheDocument();
    });
    expect(screen.getByText("plain-secret-123")).toBeInTheDocument();
    await userEv.click(screen.getByRole("button", { name: /^close$/i }));
  });
});
