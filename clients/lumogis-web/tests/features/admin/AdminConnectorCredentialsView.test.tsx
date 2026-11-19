// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// Connected services, Household view: three tabs over the same connectors view.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { AdminConnectorCredentialsView } from "../../../src/features/admin/AdminConnectorCredentialsView";
import { jsonResponse } from "../../helpers/jsonResponse";
import { seedSignedInDevice } from "../../helpers/signedInDevice";

describe("AdminConnectorCredentialsView", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
    seedSignedInDevice("a1");
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("starts on one person, then reads household and system details from their own paths", async () => {
    const admin = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, admin);
      if (u.includes("/api/v1/admin/users") && u.endsWith("/users")) return jsonResponse(200, []);
      if (u.includes("/connector-credentials/registry")) return jsonResponse(200, {});
      if (u.includes("/connector-credentials")) return jsonResponse(200, { items: [] });
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    render(
      <MemoryRouter>
        <AuthProvider client={client} tokens={store} skipRefreshOnMount>
          <AdminConnectorCredentialsView />
        </AuthProvider>
      </MemoryRouter>,
    );
    const userEv = userEvent.setup();
    expect(await screen.findByText(/choose a person to see their connected services/i)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "One person" })).toHaveAttribute("aria-selected", "true");

    await userEv.click(screen.getByRole("tab", { name: "Whole household" }));
    expect(await screen.findByText(/apply to everyone in the household/i)).toBeInTheDocument();
    await waitFor(() =>
      expect(fetchImpl.mock.calls.some((c) => String(c[0]).includes("/admin/connector-credentials/household"))).toBe(true),
    );

    await userEv.click(screen.getByRole("tab", { name: "Whole system" }));
    expect(await screen.findByText(/apply to every person in Lumogis/i)).toBeInTheDocument();
    await waitFor(() =>
      expect(fetchImpl.mock.calls.some((c) => String(c[0]).includes("/admin/connector-credentials/system"))).toBe(true),
    );
  });
});
