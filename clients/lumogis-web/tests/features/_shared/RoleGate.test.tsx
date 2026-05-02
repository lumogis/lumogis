// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// FP-046 / lumogis_web_admin_shell — RoleGate (admin vs user).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { RoleGate } from "../../../src/features/_shared/RoleGate";
import { jsonResponse } from "../../helpers/jsonResponse";

describe("RoleGate", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("renders children when the signed-in user matches the required role (admin)", async () => {
    const tokens = new AccessTokenStore();
    const user = { id: "a1", email: "admin@home.lan", role: "admin" as const };
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      return jsonResponse(404, { detail: "not found" });
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
        <RoleGate role="admin">
          <span data-testid="gate">unlocked</span>
        </RoleGate>
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("gate")).toHaveTextContent("unlocked");
    });
  });

  it("returns null for admin-only content when the user is not admin", async () => {
    const tokens = new AccessTokenStore();
    const user = { id: "u1", email: "u@home.lan", role: "user" as const };
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      return jsonResponse(404, { detail: "not found" });
    });
    const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
        <RoleGate role="admin">
          <span data-testid="gate">unlocked</span>
        </RoleGate>
        <span data-testid="public">x</span>
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("public")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("gate")).toBeNull();
  });
});
