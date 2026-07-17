// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-141 — AdminSafetyPlaygroundView: run suite + render results.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { AdminSafetyPlaygroundView } from "../../../src/features/admin/AdminSafetyPlaygroundView";
import { jsonResponse } from "../../helpers/jsonResponse";

function suiteResponse(): Response {
  return jsonResponse(200, {
    total: 24,
    passed: 23,
    failed: 0,
    warnings: 1,
    ran_at: "2026-07-14T00:00:00+00:00",
    results: [
      {
        name: "Tool result ignore-instructions",
        vector: "tool_result",
        expected: "blocked",
        actual: "blocked",
        passed: true,
        known_gap: false,
        detail: "hits=ignore_prev_instruction",
      },
      {
        name: "Base64-encoded instruction (known gap)",
        vector: "document_ingest",
        expected: "flagged",
        actual: "passed",
        passed: false,
        known_gap: true,
        detail: "no pattern hit",
      },
    ],
  });
}

function setup() {
  const user = { id: "admin", email: "admin@home.lan", role: "admin" as const };
  const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/v1/auth/me")) return jsonResponse(200, user);
    if (url.includes("/api/v1/admin/safety/run") && init?.method === "POST") return suiteResponse();
    return jsonResponse(404, { detail: "not found" });
  });
  const tokens = new AccessTokenStore();
  const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
  return { client, tokens, fetchImpl };
}

function renderView(client: ApiClient, tokens: AccessTokenStore) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
        <AdminSafetyPlaygroundView />
      </AuthProvider>
    </QueryClientProvider>,
  );
}

describe("AdminSafetyPlaygroundView", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("runs the suite and renders the pass/fail summary + rows", async () => {
    const { client, tokens } = setup();
    const user = userEvent.setup();
    renderView(client, tokens);

    await user.click(screen.getByRole("button", { name: /run injection test suite/i }));

    await waitFor(() => {
      expect(screen.getByTestId("safety-summary")).toBeInTheDocument();
    });
    expect(screen.getByTestId("safety-summary")).toHaveTextContent("23/24 passed");
    expect(screen.getByTestId("safety-summary")).toHaveTextContent(/known-gap warning/i);
    expect(screen.getByText("Tool result ignore-instructions")).toBeInTheDocument();
  });
});
