// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// FP-046 — MeConnectorsView: registry failure shows hint banner; list still drives rows.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { MeConnectorsView } from "../../../src/features/me/MeConnectorsView";
import { jsonResponse } from "../../helpers/jsonResponse";

describe("MeConnectorsView", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("shows fallback banner when the registry call fails but credentials list loads", async () => {
    const user = { id: "u1", email: "u@home.lan", role: "user" as const };
    const listItems = { items: [{ connector: "cal_dav_1", updated_at: "2020-01-01" }] };
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/registry")) return new Response("bad", { status: 500 });
      if (u.includes("/connector-credentials") && !u.includes("registry")) return jsonResponse(200, listItems);
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MeConnectorsView />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(
        screen.getByText(/Connector schema hints unavailable; using JSON fallback for listed connectors\./i),
      ).toBeInTheDocument();
    });
    expect(screen.getByText("cal_dav_1")).toBeInTheDocument();
  });
});
