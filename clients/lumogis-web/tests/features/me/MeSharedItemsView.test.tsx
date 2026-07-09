// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-583 — MeSharedItemsView: lists the member's shares, per-row unshare calls
// the correct per-type unpublish route and removes the row, empty + error states.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { MeSharedItemsView } from "../../../src/features/me/MeSharedItemsView";
import { jsonResponse } from "../../helpers/jsonResponse";

const ME = { id: "alice", email: "alice@home.lan", role: "user" as const };

const ITEMS = [
  { resource_type: "files", resource_id: "7", label: "tax.pdf", shared_at: "2026-06-01T00:00:00Z" },
  { resource_type: "entities", resource_id: "e-1", label: "Acme Corp", shared_at: null },
];

function setup(opts: { deleteStatus?: number; emptyList?: boolean } = {}) {
  const deleteStatus = opts.deleteStatus ?? 204;
  const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/v1/auth/me")) return jsonResponse(200, ME);
    // Unshare reuses the per-type publish route.
    if (url.includes("/publish") && init?.method === "DELETE") {
      if (deleteStatus >= 400) {
        return jsonResponse(deleteStatus, {
          detail: { error: "unpublish_failed", message: "Couldn't unshare — please retry." },
        });
      }
      return new Response(null, { status: 204 });
    }
    if (url.includes("/api/v1/me/shared-items")) {
      return jsonResponse(200, { items: opts.emptyList ? [] : ITEMS });
    }
    return jsonResponse(404, { detail: "not found" });
  });
  const tokens = new AccessTokenStore();
  const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
  return { client, tokens, fetchImpl };
}

function renderView(client: ApiClient, tokens: AccessTokenStore) {
  return render(
    <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
      <MeSharedItemsView />
    </AuthProvider>,
  );
}

describe("MeSharedItemsView", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("lists my shared items and unshares via the correct per-type route", async () => {
    const { client, tokens, fetchImpl } = setup();
    const ev = userEvent.setup();
    renderView(client, tokens);

    await screen.findByTestId("me-shared-items");
    expect(await screen.findByText("tax.pdf")).toBeInTheDocument();
    expect(screen.getByText("Acme Corp")).toBeInTheDocument();

    // Unshare the document → confirm → DELETE /api/v1/files/7/publish.
    await ev.click(screen.getByTestId("unshare-files:7"));
    expect(screen.getByTestId("confirm-files:7")).toHaveTextContent(
      /unshare this document from your household\?/i,
    );
    await ev.click(screen.getByTestId("confirm-unshare-files:7"));

    await waitFor(() => {
      const del = fetchImpl.mock.calls.filter(
        (c) =>
          String(c[0]).includes("/api/v1/files/7/publish") &&
          (c[1] as RequestInit)?.method === "DELETE",
      );
      expect(del.length).toBe(1);
    });
    // List refetched after success.
    await waitFor(() => {
      const gets = fetchImpl.mock.calls.filter(
        (c) =>
          String(c[0]).includes("/api/v1/me/shared-items") &&
          (c[1] as RequestInit)?.method !== "DELETE",
      );
      expect(gets.length).toBeGreaterThanOrEqual(2);
    });
  });

  it("shows the empty state when nothing is shared", async () => {
    const { client, tokens } = setup({ emptyList: true });
    renderView(client, tokens);
    expect(
      await screen.findByText(/haven't shared anything with your household yet/i),
    ).toBeInTheDocument();
  });

  it("surfaces the human error message when unshare fails", async () => {
    const { client, tokens } = setup({ deleteStatus: 500 });
    const ev = userEvent.setup();
    renderView(client, tokens);

    await screen.findByText("tax.pdf");
    await ev.click(screen.getByTestId("unshare-files:7"));
    await ev.click(screen.getByTestId("confirm-unshare-files:7"));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Couldn't unshare — please retry.");
    expect(alert.textContent ?? "").not.toContain("unpublish_failed");
  });
});
