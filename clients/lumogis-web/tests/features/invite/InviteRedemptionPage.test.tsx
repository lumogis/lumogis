// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { INVITE_ONBOARDING_STORAGE_KEY } from "../../../src/api/invites";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { InviteRedemptionPage } from "../../../src/features/invite/InviteRedemptionPage";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("InviteRedemptionPage", () => {
  let originalFetch: typeof fetch;
  const tokens = new AccessTokenStore();

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    tokens.clear();
    sessionStorage.clear();
    window.history.replaceState({}, "", "/");
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
    sessionStorage.clear();
  });

  function renderPage(token = "linv_abcdefghijklmnopqrstuvwxyz123456789"): void {
    render(
      <MemoryRouter initialEntries={[`/invite?token=${encodeURIComponent(token)}`]}>
        <Routes>
          <Route
            path="/invite"
            element={
              <AuthProvider tokens={tokens} skipRefreshOnMount>
                <InviteRedemptionPage />
              </AuthProvider>
            }
          />
          <Route path="/chat" element={<div>Chat home</div>} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("happy path stores onboarding hint and access token after redeem", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/redeem") && init?.method === "POST") {
        return jsonResponse(200, {
          access_token: "invite-tok",
          token_type: "bearer",
          expires_in: 900,
          user: { id: "u1", email: "new@example.com", role: "user" },
          invite_onboarding: { allows_shared: true },
        });
      }
      if (url.includes("/invites/") && !url.includes("/redeem")) {
        return jsonResponse(200, {
          allows_shared: true,
          expires_at: new Date(Date.now() + 3600_000).toISOString(),
        });
      }
      if (url.includes("/auth/me")) {
        return jsonResponse(200, { id: "u1", email: "new@example.com", role: "user" });
      }
      return jsonResponse(404, { detail: "not found" });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    renderPage();

    await waitFor(() => {
      expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    });
    expect(window.location.pathname).toBe("/invite");
    expect(window.location.search).toBe("");

    await user.type(screen.getByLabelText(/email/i), "new@example.com");
    await user.type(screen.getByLabelText(/password/i), "securepass1234");
    await user.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() => {
      expect(tokens.get()).toBe("invite-tok");
    });
    const stored = sessionStorage.getItem(INVITE_ONBOARDING_STORAGE_KEY);
    expect(stored).toContain("allows_shared");
    await waitFor(() => {
      expect(screen.getByText("Chat home")).toBeInTheDocument();
    });
  });

  it("shows error for invalid token peek", async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse(404, { detail: "Invite link is invalid or expired" }),
    ) as unknown as typeof fetch;
    renderPage("linv_bad");
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/invalid or expired/i);
    });
  });
});
