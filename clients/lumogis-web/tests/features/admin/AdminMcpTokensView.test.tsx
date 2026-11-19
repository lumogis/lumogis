// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-527 — AdminMcpTokensView renders per-token access labels in the list.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
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
    await userEv.click(await screen.findByRole("combobox", { name: /^user$/i }));
    await userEv.click(await screen.findByRole("option", { name: /tom@home\.lan \(Member\)/ }));

    await waitFor(() => expect(screen.getByText("legacy")).toBeInTheDocument());
    // Access labels render one per token, distinct per scope shape.
    expect(screen.getByText(/Unrestricted \(legacy\)\./)).toBeInTheDocument();
    expect(screen.getByText(/Read-only\./)).toBeInTheDocument();
    expect(screen.getByText(/Read \+ write\./)).toBeInTheDocument();
    // Dates are the house format, and no raw id is shown.
    expect(screen.getAllByText(/Created 1 Jan 2020/).length).toBe(1);
    expect(screen.queryByText("tk1")).toBeNull();

    // LUM-530: admins list + revoke only — no Mint control (the old admin Mint
    // button POSTed to a non-existent route and 405'd).
    expect(screen.queryByRole("button", { name: /^mint$/i })).toBeNull();
    expect(screen.getAllByRole("button", { name: /^actions for /i }).length).toBe(tokens.length);
  });

  function mountWithTokens(tokens: unknown[], fetchSpy?: (u: string, init?: RequestInit) => Response | null) {
    const admin = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    const users = [{ id: "u-t", email: "tom@home.lan", role: "user", disabled: false }];
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, admin);
      if (u.includes("/api/v1/auth/refresh")) return jsonResponse(200, { access_token: "x", user: admin });
      const hit = fetchSpy?.(u, init);
      if (hit) return hit;
      if (u.includes("/api/v1/admin/users") && u.endsWith("/users")) return jsonResponse(200, users);
      if (u.includes("/api/v1/admin/users/u-t/mcp-tokens")) return jsonResponse(200, tokens);
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <AdminMcpTokensView />
      </AuthProvider>,
    );
    return fetchImpl;
  }

  async function pickTom(userEv: ReturnType<typeof userEvent.setup>) {
    await userEv.click(await screen.findByRole("combobox", { name: /^user$/i }));
    await userEv.click(await screen.findByRole("option", { name: /tom@home\.lan/ }));
  }

  it("asks to choose a person first, and shows an empty state when they have no keys", async () => {
    mountWithTokens([]);
    expect(await screen.findByText(/choose a person to list their keys/i)).toBeInTheDocument();
    await pickTom(userEvent.setup());
    expect(await screen.findByText("No keys")).toBeInTheDocument();
  });

  it("Revoke is confirmed: Cancel does nothing, confirming sends DELETE; revoked keys have no Revoke", async () => {
    const tokens = [
      { id: "tk1", user_id: "u-t", label: "claude", created_at: "2020-01-01T00:00:00Z", scopes: ["mcp:read"] },
      {
        id: "tk2",
        user_id: "u-t",
        label: "old",
        created_at: "2020-01-02T00:00:00Z",
        scopes: ["mcp:read"],
        revoked_at: "2020-02-01T00:00:00Z",
      },
    ];
    const fetchImpl = mountWithTokens(tokens, (u, init) =>
      u.includes("/mcp-tokens/tk1") && init?.method === "DELETE" ? jsonResponse(200, {}) : null,
    );
    const userEv = userEvent.setup();
    await pickTom(userEv);
    await screen.findByText("claude");
    expect(screen.getByText("Revoked")).toBeInTheDocument();
    const deleted = () => fetchImpl.mock.calls.some((c) => c[1]?.method === "DELETE");

    await userEv.click(screen.getByRole("button", { name: "Actions for old" }));
    expect(await screen.findByRole("menuitem", { name: /^revoke$/i })).toHaveAttribute("aria-disabled", "true");
    await userEv.keyboard("{Escape}");

    await userEv.click(screen.getByRole("button", { name: "Actions for claude" }));
    await userEv.click(await screen.findByRole("menuitem", { name: /^revoke$/i }));
    let dialog = await screen.findByRole("alertdialog");
    await userEv.click(within(dialog).getByRole("button", { name: /^cancel$/i }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(deleted()).toBe(false);

    await userEv.click(screen.getByRole("button", { name: "Actions for claude" }));
    await userEv.click(await screen.findByRole("menuitem", { name: /^revoke$/i }));
    dialog = await screen.findByRole("alertdialog");
    await userEv.click(within(dialog).getByRole("button", { name: /^revoke$/i }));
    await waitFor(() => expect(deleted()).toBe(true));
  });

  it("shows a retry state when the keys cannot load", async () => {
    mountWithTokens([], (u) => (u.includes("/mcp-tokens") ? jsonResponse(500, {}) : null));
    await pickTom(userEvent.setup());
    expect(await screen.findByText(/couldn't load their keys/i)).toBeInTheDocument();
  });
});
