// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-527 — AdminMcpTokensView renders per-token access labels in the list.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { AdminMcpTokensView } from "../../../src/features/admin/AdminMcpTokensView";
import { jsonResponse } from "../../helpers/jsonResponse";

describe("AdminMcpTokensView", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("shows per-token access labels for the selected user's tokens (LUM-527)", async () => {
    const admin = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    const users = [{ id: "u-t", email: "tom@home.lan", role: "user", disabled: false }];
    const tokens = [
      { id: "tk1", user_id: "u-t", label: "legacy", created_at: "2020-01-01T00:00:00Z", scopes: null },
      { id: "tk2", user_id: "u-t", label: "ro", created_at: "2020-01-02T00:00:00Z", scopes: ["mcp:read"] },
      { id: "tk3", user_id: "u-t", label: "rw", created_at: "2020-01-03T00:00:00Z", scopes: ["mcp:read", "mcp:write"] },
    ];

    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, admin);
      if (u.includes("/api/v1/auth/refresh")) return jsonResponse(200, { access_token: "x", user: admin });
      if (u.includes("/api/v1/admin/users") && u.endsWith("/users")) return jsonResponse(200, users);
      if (u.includes("/api/v1/admin/users/u-t/mcp-tokens")) return jsonResponse(200, tokens);
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });

    const userEv = userEvent.setup();
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <AdminMcpTokensView />
      </AuthProvider>,
    );

    // Pick the target user → triggers the per-user token list fetch.
    await waitFor(() => expect(screen.getByLabelText(/^user$/i)).toBeInTheDocument());
    await userEv.selectOptions(screen.getByLabelText(/^user$/i), "u-t");

    await waitFor(() => expect(screen.getByText("legacy")).toBeInTheDocument());
    // Access labels render one per token (bracketed form, distinct per scope shape).
    expect(screen.getByText(/\[Unrestricted \(legacy\)\]/)).toBeInTheDocument();
    expect(screen.getByText(/\[Read-only\]/)).toBeInTheDocument();
    expect(screen.getByText(/\[Read \+ write\]/)).toBeInTheDocument();

    // LUM-530: admins list + revoke only — no Mint control (the old admin Mint
    // button POSTed to a non-existent route and 405'd).
    expect(screen.queryByRole("button", { name: /^mint$/i })).toBeNull();
    expect(screen.getAllByRole("button", { name: /^revoke$/i }).length).toBe(tokens.length);
  });
});
