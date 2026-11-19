// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { MeProfileView } from "../../../src/features/me/MeProfileView";
import { jsonResponse } from "../../helpers/jsonResponse";

describe("MeProfileView", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("opens change-password form and submits to /api/v1/me/password", async () => {
    const user = { id: "u1", email: "u@home.lan", role: "user" as const };
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/me/password") && init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toEqual({
          current_password: "oldpassword1234",
          new_password: "newpassword1234",
        });
        return jsonResponse(200, { ok: true });
      }
      if (u.includes("/api/v1/auth/logout")) return jsonResponse(200, { ok: true });
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    const userEv = userEvent.setup();

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MeProfileView />
      </AuthProvider>,
    );
    await screen.findByText("u@home.lan");
    await userEv.click(screen.getByRole("button", { name: /change password/i }));
    await userEv.type(screen.getByLabelText(/current password/i), "oldpassword1234");
    await userEv.type(screen.getByLabelText(/^new password/i), "newpassword1234");
    await userEv.type(screen.getByLabelText(/confirm new password/i), "newpassword1234");
    await userEv.click(screen.getByRole("button", { name: /save new password/i }));
    await waitFor(() => {
      expect(fetchImpl).toHaveBeenCalled();
    });
    const pwCalls = fetchImpl.mock.calls.filter((c) => String(c[0]).includes("/me/password"));
    expect(pwCalls.length).toBeGreaterThanOrEqual(1);
  });

  it("blocks submit when confirmation mismatches", async () => {
    const user = { id: "u1", email: "u@home.lan", role: "user" as const };
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    const userEv = userEvent.setup();

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MeProfileView />
      </AuthProvider>,
    );
    await screen.findByText("u@home.lan");
    await userEv.click(screen.getByRole("button", { name: /change password/i }));
    await userEv.type(screen.getByLabelText(/current password/i), "oldpassword1234");
    await userEv.type(screen.getByLabelText(/^new password/i), "newpassword1234");
    await userEv.type(screen.getByLabelText(/confirm new password/i), "otherpassword123");
    await userEv.click(screen.getByRole("button", { name: /save new password/i }));
    expect(screen.getByRole("alert")).toHaveTextContent(/do not match/i);
    expect(fetchImpl.mock.calls.some((c) => String(c[0]).includes("/me/password"))).toBe(false);
  });

  // LUM-885: the form says what saving revokes, with the member's real token count.
  function mountWithTokens(tokensResponse: () => Response) {
    const user = { id: "u1", email: "u@home.lan", role: "user" as const };
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/me/mcp-tokens")) return tokensResponse();
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MeProfileView />
      </AuthProvider>,
    );
    return fetchImpl;
  }
  const tokenRow = (id: string) => ({
    id,
    label: id,
    created_at: "2026-01-01T00:00:00Z",
    scopes: ["mcp:read"],
  });

  it("shows MCP revocation count when the form opens", async () => {
    const fetchImpl = mountWithTokens(() => jsonResponse(200, [tokenRow("a"), tokenRow("b")]));
    const userEv = userEvent.setup();
    await screen.findByText("u@home.lan");
    expect(fetchImpl.mock.calls.some((c) => String(c[0]).includes("/me/mcp-tokens"))).toBe(false);
    await userEv.click(screen.getByRole("button", { name: /change password/i }));
    await waitFor(() => {
      expect(screen.getByTestId("password-change-consequence")).toHaveTextContent(
        /signs you out on every device and revokes your 2 MCP tokens/i,
      );
    });
  });

  it("uses the singular noun for exactly one token", async () => {
    mountWithTokens(() => jsonResponse(200, [tokenRow("a")]));
    const userEv = userEvent.setup();
    await screen.findByText("u@home.lan");
    await userEv.click(screen.getByRole("button", { name: /change password/i }));
    await waitFor(() => {
      expect(screen.getByTestId("password-change-consequence")).toHaveTextContent(
        /revokes your 1 MCP token\./i,
      );
    });
  });

  it("falls back to generic copy when the token list fails or is empty", async () => {
    mountWithTokens(() => jsonResponse(500, {}));
    const userEv = userEvent.setup();
    await screen.findByText("u@home.lan");
    await userEv.click(screen.getByRole("button", { name: /change password/i }));
    const el = await screen.findByTestId("password-change-consequence");
    await waitFor(() => {
      expect(el).toHaveTextContent(/revokes any MCP tokens you hold/i);
    });
    expect(el.textContent).not.toMatch(/\d/);
  });

  it("uses the generic copy, with no number, when the member holds no tokens", async () => {
    mountWithTokens(() => jsonResponse(200, []));
    const userEv = userEvent.setup();
    await screen.findByText("u@home.lan");
    await userEv.click(screen.getByRole("button", { name: /change password/i }));
    const el = await screen.findByTestId("password-change-consequence");
    await waitFor(() => {
      expect(el).toHaveTextContent(/revokes any MCP tokens you hold/i);
    });
    expect(el.textContent).not.toMatch(/\d/);
  });
});
