// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// FP-046 — MeMcpTokensView list + create a key + the one-time key dialog.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { MeMcpTokensView } from "../../../src/features/me/MeMcpTokensView";
import { jsonResponse } from "../../helpers/jsonResponse";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

// LUM-886: this suite mounts a real AuthProvider; mark the device as already reconciled so the
// first-sign-in wipe does not remove the view's own queries.
beforeEach(() => {
  seedSignedInDevice("u1");
});

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
      { id: "t1", label: "a", created_at: "2020-01-01T00:00:00Z", scopes: null },
    ];
    const refreshBody = { access_token: "x", user };

    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/auth/refresh")) return jsonResponse(200, refreshBody);
      if (u.includes("/me/mcp-tokens") && init?.method === "POST")
        return jsonResponse(201, {
          plaintext: "plain-secret-123",
          token: { id: "new1", label: "l", created_at: "2020-01-02T00:00:00Z", scopes: ["mcp:read"] },
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
    // Plain wording: a date and an access word, never the id.
    expect(screen.queryByText("t1")).toBeNull();
    expect(screen.getByText(/Created 1 Jan 2020/)).toBeInTheDocument();

    await userEv.type(screen.getByLabelText("Key name"), "my");
    await userEv.click(screen.getByRole("button", { name: /^create a key$/i }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /your new key/i })).toBeInTheDocument();
    });
    expect(screen.getByText("plain-secret-123")).toBeInTheDocument();
    await userEv.click(screen.getByRole("button", { name: /^close$/i }));
  });

  it("defaults to read-only and sends the chosen scopes on mint (LUM-527)", async () => {
    const user = { id: "u1", email: "u@home.lan", role: "user" as const };
    const mcpList = [
      { id: "t1", label: "legacy-tok", created_at: "2020-01-01T00:00:00Z", scopes: null },
      { id: "t2", label: "rw-tok", created_at: "2020-01-02T00:00:00Z", scopes: ["mcp:read", "mcp:write"] },
    ];
    const refreshBody = { access_token: "x", user };
    const postBodies: unknown[] = [];

    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/auth/refresh")) return jsonResponse(200, refreshBody);
      if (u.includes("/me/mcp-tokens") && init?.method === "POST") {
        postBodies.push(JSON.parse(String(init?.body)));
        return jsonResponse(201, {
          plaintext: "p",
          token: { id: "newX", label: "x", created_at: "2020-01-03T00:00:00Z", scopes: ["mcp:read"] },
        });
      }
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

    // List renders per-token access labels (null ⇒ legacy; write ⇒ read+write).
    await waitFor(() => expect(screen.getByText("legacy-tok")).toBeInTheDocument());
    expect(screen.getByText(/Unrestricted \(legacy\)/)).toBeInTheDocument();
    expect(screen.getByText(/Created 2 Jan 2020, \d\d:\d\d\. Read \+ write\./)).toBeInTheDocument();

    // Default selection is Read-only → first key sends ["mcp:read"].
    expect(screen.getByRole("radio", { name: "Read-only" })).toBeChecked();
    await userEv.type(screen.getByLabelText("Key name"), "a");
    await userEv.click(screen.getByRole("button", { name: /^create a key$/i }));
    await waitFor(() => expect(postBodies).toHaveLength(1));
    expect(postBodies[0]).toEqual({ label: "a", scopes: ["mcp:read"] });
    await userEv.click(screen.getByRole("button", { name: /^close$/i }));

    // Opt into Read + write → next mint sends both scopes.
    await userEv.click(screen.getByRole("radio", { name: "Read + write" }));
    await userEv.type(screen.getByLabelText("Key name"), "b");
    await userEv.click(screen.getByRole("button", { name: /^create a key$/i }));
    await waitFor(() => expect(postBodies).toHaveLength(2));
    expect(postBodies[1]).toEqual({ label: "b", scopes: ["mcp:read", "mcp:write"] });
  });

  function revokeHarness() {
    const user = { id: "u1", email: "u@home.lan", role: "user" as const };
    let mcpList = [{ id: "t1", label: "Claude Desktop", created_at: "2020-01-01T00:00:00Z", scopes: ["mcp:read"] }];
    const deleted: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/me/mcp-tokens/") && init?.method === "DELETE") {
        deleted.push(u);
        mcpList = [];
        return new Response(null, { status: 204 });
      }
      if (u.includes("/me/mcp-tokens")) return jsonResponse(200, mcpList);
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MeMcpTokensView />
      </AuthProvider>,
    );
    return { deleted };
  }

  it("Revoke is in the row menu and asks first: Cancel does nothing, Revoke deletes", async () => {
    const { deleted } = revokeHarness();
    const userEv = userEvent.setup();
    await userEv.click(await screen.findByRole("button", { name: "Actions for Claude Desktop" }));
    await userEv.click(await screen.findByRole("menuitem", { name: "Revoke" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent(/loses access to Lumogis/);
    await userEv.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(deleted).toHaveLength(0);

    await userEv.click(screen.getByRole("button", { name: "Actions for Claude Desktop" }));
    await userEv.click(await screen.findByRole("menuitem", { name: "Revoke" }));
    await userEv.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(deleted).toHaveLength(1));
    expect(deleted[0]).toMatch(/\/me\/mcp-tokens\/t1$/);
    // The list refreshes to the empty state with the next step.
    expect(await screen.findByText("No keys yet")).toBeInTheDocument();
  });

  it("shows an error with retry when the keys cannot load", async () => {
    const user = { id: "u1", email: "u@home.lan", role: "user" as const };
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      return jsonResponse(500, { detail: "boom" });
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MeMcpTokensView />
      </AuthProvider>,
    );
    expect(await screen.findByText(/couldn't load your keys/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
