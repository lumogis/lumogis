// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// Phase 3.5 — non-admin /admin keeps the "Admin only" toast state.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { AdminPage } from "../../../src/features/admin/AdminPage";
import { jsonResponse } from "../../helpers/jsonResponse";

function ChatProbe(): JSX.Element {
  const loc = useLocation();
  const toast = (loc.state as { toast?: string } | null)?.toast ?? "none";
  return <p role="status">{toast}</p>;
}

describe("AdminPage", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("redirects non-admins to chat while preserving the Admin only toast", async () => {
    const user = { id: "u1", email: "user@home.lan", role: "user" as const };
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      return jsonResponse(404, { detail: "not found" });
    });
    const tokens = new AccessTokenStore();
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
        <MemoryRouter initialEntries={["/admin"]}>
          <Routes>
            <Route path="/admin" element={<AdminPage />} />
            <Route path="/chat" element={<ChatProbe />} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("Admin only");
    });
  });

  it("renders subshell for admin with nested route", async () => {
    const admin = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const url = String(input);
      if (url.includes("/api/v1/auth/me")) return jsonResponse(200, admin);
      return jsonResponse(404, { detail: "not found" });
    });
    const tokens = new AccessTokenStore();
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });

    const { container } = render(
      <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
        <MemoryRouter initialEntries={["/admin/users"]}>
          <Routes>
            <Route path="/admin" element={<AdminPage />}>
              <Route path="users" element={<h1>Users</h1>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Users" })).toBeInTheDocument();
    });
    expect(container.querySelector(".lumogis-subshell.lumogis-subshell--admin")).not.toBeNull();
    expect(screen.getByRole("navigation", { name: /^administration$/i })).toBeInTheDocument();
  });
});
