// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-885 — AdminUsersView says what a password reset, demotion or deletion revokes.
// Kept apart from AdminUsersView.test.tsx, which another branch edits.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { AdminUsersView } from "../../../src/features/admin/AdminUsersView";
import { jsonResponse } from "../../helpers/jsonResponse";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

const admin = { id: "a1", email: "admin@home.lan", role: "admin" as const };
const row = (id: string, email: string, role: "admin" | "user") => ({
  id,
  email,
  role,
  disabled: false,
  created_at: "2020-01-01T00:00:00Z",
  last_login_at: null,
  last_seen_at: null,
});
const tok = (id: string, revoked: boolean) => ({
  id,
  user_id: "t1",
  label: id,
  created_at: "2026-01-01T00:00:00Z",
  scopes: ["mcp:read"],
  revoked_at: revoked ? "2026-02-01T00:00:00Z" : null,
});

describe("AdminUsersView — credential consequence copy (LUM-885)", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
    // LUM-886: a real AuthProvider is mounted; mark the device as already reconciled so
    // the first-sign-in wipe does not remove the view's own queries.
    seedSignedInDevice(admin.id);
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  function mount(users: unknown[], tokens?: unknown[]) {
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, admin);
      if (u.includes("/mcp-tokens")) return jsonResponse(200, tokens ?? []);
      if (u.includes("/api/v1/admin/users") && u.endsWith("/users") && !u.includes("users/"))
        return jsonResponse(200, users);
      if (init?.method === "PATCH" || init?.method === "DELETE") return jsonResponse(200, { ok: true });
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <AdminUsersView />
      </AuthProvider>,
    );
    return fetchImpl;
  }

  async function openReset() {
    const userEv = userEvent.setup();
    const bob = await screen.findByRole("row", { name: /bob@home\.lan/i });
    await userEv.click(within(bob).getByRole("button", { name: /more actions/i }));
    await userEv.click(within(bob).getByRole("menuitem", { name: /^reset password$/i }));
    return screen.findByTestId("admin-reset-consequence");
  }

  const users = [row("a1", "admin@home.lan", "admin"), row("t1", "bob@home.lan", "user")];

  it("reset form counts only the target's active tokens (revoked rows excluded)", async () => {
    mount(users, [tok("a", false), tok("b", false), tok("c", true)]);
    const el = await openReset();
    await waitFor(() => {
      expect(el).toHaveTextContent(
        "Saving signs bob@home.lan out on every device and revokes their 2 MCP tokens.",
      );
    });
  });

  it("reset form uses the singular noun for exactly one active token", async () => {
    mount(users, [tok("a", false), tok("b", true)]);
    const el = await openReset();
    await waitFor(() => {
      expect(el).toHaveTextContent("revokes their 1 MCP token.");
    });
  });

  it("with no active tokens the reset copy is the generic fallback and shows no digit", async () => {
    mount(users, [tok("a", true), tok("b", true)]);
    const el = await openReset();
    await waitFor(() => {
      expect(el).toHaveTextContent(
        "Saving signs bob@home.lan out on every device and revokes any MCP tokens they hold.",
      );
    });
    expect(el.textContent).not.toMatch(/\d/);
  });

  it("demote confirm says what is revoked and which invites are cancelled", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    mount([row("a1", "admin@home.lan", "admin"), row("a2", "carol@home.lan", "admin")]);
    const userEv = userEvent.setup();
    const carol = await screen.findByRole("row", { name: /carol@home\.lan/i });
    await userEv.click(within(carol).getByRole("button", { name: /^make member$/i }));
    const message = String(confirmSpy.mock.calls[0]?.[0]);
    expect(message).toContain("signed out on every device");
    expect(message).toContain("their MCP tokens revoked");
    expect(message).toContain("any invites they sent cancelled");
  });

  it("promote confirm is unchanged (promotion revokes nothing)", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    mount(users);
    const userEv = userEvent.setup();
    const bob = await screen.findByRole("row", { name: /bob@home\.lan/i });
    await userEv.click(within(bob).getByRole("button", { name: /^make admin$/i }));
    expect(String(confirmSpy.mock.calls[0]?.[0])).not.toContain("signed out");
  });

  it("delete confirm says what is revoked, and cancelling sends no DELETE", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const fetchImpl = mount(users);
    const userEv = userEvent.setup();
    const bob = await screen.findByRole("row", { name: /bob@home\.lan/i });
    await userEv.click(within(bob).getByRole("button", { name: /more actions/i }));
    await userEv.click(within(bob).getByRole("menuitem", { name: /^delete$/i }));
    const message = String(confirmSpy.mock.calls[0]?.[0]);
    expect(message).toContain("their MCP tokens revoked");
    expect(message).toContain("any invites they sent cancelled");
    expect(fetchImpl.mock.calls.some((c) => c[1]?.method === "DELETE")).toBe(false);
  });
});
