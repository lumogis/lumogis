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
});
