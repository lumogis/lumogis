// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-649 — MeRelevanceView: durable "what I've tuned" learned-weights view.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { MeRelevanceView } from "../../../src/features/me/MeRelevanceView";
import { jsonResponse } from "../../helpers/jsonResponse";

function renderView(fetchImpl: typeof fetch) {
  const store = new AccessTokenStore();
  const client = new ApiClient({ tokens: store, fetchImpl });
  return render(
    <AuthProvider client={client} tokens={store} skipRefreshOnMount>
      <MeRelevanceView />
    </AuthProvider>,
  );
}

function withLearned(body: unknown): typeof fetch {
  return vi.fn(async (input: RequestInfo) => {
    const u = String(input);
    if (u.includes("/auth/me")) return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
    if (u.includes("/profile/learned")) return jsonResponse(200, body);
    return jsonResponse(404, {});
  }) as unknown as typeof fetch;
}

describe("MeRelevanceView", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("renders demoted and promoted terms", async () => {
    renderView(
      withLearned({
        topics: [{ term: "newsletter-digest", weight: -0.45 }, { term: "work", weight: 0.3 }],
        entities: [],
        categories: [{ term: "newsletter", weight: -0.6 }],
      }),
    );
    await waitFor(() => expect(screen.getByText("newsletter-digest")).toBeInTheDocument());
    expect(screen.getByText("work")).toBeInTheDocument();
    expect(screen.getByText("newsletter")).toBeInTheDocument();
    expect(screen.getAllByText(/Flagging fewer like these/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Flagging more like these/)).toBeInTheDocument();
  });

  it("shows an empty state when nothing has been tuned", async () => {
    renderView(withLearned({ topics: [], entities: [], categories: [] }));
    await waitFor(() =>
      expect(screen.getByTestId("relevance-empty")).toBeInTheDocument(),
    );
    expect(screen.getByText(/Nothing tuned yet/)).toBeInTheDocument();
  });

  it("shows an error with a retry when the learned weights cannot be loaded", async () => {
    renderView(
      vi.fn(async (input: RequestInfo) => {
        const u = String(input);
        if (u.includes("/auth/me")) return jsonResponse(200, { id: "u1", email: "u@home.lan", role: "user" });
        return jsonResponse(500, { detail: "boom" });
      }) as unknown as typeof fetch,
    );
    expect(await screen.findByText("Lumogis couldn't load what it has learned.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
