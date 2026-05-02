// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// FP-046 — MePermissionsView: list + mode change (PUT + invalidate list).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { MePermissionsView } from "../../../src/features/me/MePermissionsView";
import { jsonResponse } from "../../helpers/jsonResponse";

describe("MePermissionsView", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("renders permissions and sends PUT when mode changes", async () => {
    const user = { id: "u1", email: "u@home.lan", role: "user" as const };
    const perms1 = [
      { connector: "c1", mode: "ASK" as const, is_default: false, updated_at: "2020-01-01T00:00:00Z" },
    ];
    const perms2 = [
      { ...perms1[0]!, mode: "DO" as const },
    ];
    let listFetch = 0;
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/me/permissions/") && init?.method === "PUT") {
        expect(JSON.parse(String(init.body))).toEqual({ mode: "DO" });
        return new Response(null, { status: 204 });
      }
      if (u.includes("/api/v1/me/permissions") && (init?.method === "GET" || init?.method === undefined)) {
        listFetch += 1;
        return jsonResponse(200, listFetch >= 2 ? perms2 : perms1);
      }
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });
    const userEv = userEvent.setup();

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MePermissionsView />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText("c1")).toBeInTheDocument();
    });
    const select = screen.getByRole("combobox");
    expect(select).toHaveValue("ASK");
    await userEv.selectOptions(select, "DO");
    await waitFor(() => {
      const putCalls = fetchImpl.mock.calls.filter(
        (c) => String(c[0]).includes("/me/permissions") && (c[1] as RequestInit)?.method === "PUT",
      );
      expect(putCalls.length).toBeGreaterThan(0);
    });
  });
});
