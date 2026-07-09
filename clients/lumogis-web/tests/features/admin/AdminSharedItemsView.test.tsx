// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// LUM-584 — AdminSharedItemsView: admin can review + retract household shares;
// the affordance mirrors the server admin gate (never renders for a member),
// names the owner in the confirm, and surfaces errors.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { AdminSharedItemsView } from "../../../src/features/admin/AdminSharedItemsView";
import { jsonResponse } from "../../helpers/jsonResponse";

const ADMIN = { id: "carol", email: "carol@home.lan", role: "admin" as const };
const MEMBER = { id: "bob", email: "bob@home.lan", role: "user" as const };

const ITEMS = [
  { resource_type: "files", resource_id: "42", source_owner_id: "bob", label: "insurance.pdf" },
  { resource_type: "notes", resource_id: "n-1", source_owner_id: "bob", label: "grocery list" },
];

function setup(opts: { me: typeof ADMIN | typeof MEMBER; deleteStatus?: number }) {
  const deleteStatus = opts.deleteStatus ?? 200;
  const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/v1/auth/me")) return jsonResponse(200, opts.me);
    if (url.includes("/api/v1/admin/shared-items") && init?.method === "DELETE") {
      if (deleteStatus >= 400) {
        return jsonResponse(deleteStatus, {
          detail: {
            error: "unshare_incomplete",
            message: "Couldn't fully unshare — please retry.",
          },
        });
      }
      return jsonResponse(200, {
        resource_type: "files",
        resource_id: "42",
        source_owner_id: "bob",
        unshared: true,
      });
    }
    if (url.includes("/api/v1/admin/shared-items")) return jsonResponse(200, { items: ITEMS });
    return jsonResponse(404, { detail: "not found" });
  });
  const tokens = new AccessTokenStore();
  const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
  return { client, tokens, fetchImpl };
}

function renderView(client: ApiClient, tokens: AccessTokenStore) {
  return render(
    <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
      <AdminSharedItemsView />
    </AuthProvider>,
  );
}

describe("AdminSharedItemsView", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("lists household shares and force-unshares after an owner-named confirm", async () => {
    const { client, tokens, fetchImpl } = setup({ me: ADMIN });
    const ev = userEvent.setup();
    renderView(client, tokens);

    // Rows render for the admin.
    await screen.findByTestId("admin-shared-items");
    expect(await screen.findByText("insurance.pdf")).toBeInTheDocument();
    expect(screen.getByText("grocery list")).toBeInTheDocument();

    // Force unshare → inline confirm naming the owner.
    await ev.click(screen.getByTestId("force-unshare-files:42"));
    const confirm = screen.getByTestId("confirm-files:42");
    expect(confirm).toHaveTextContent(/unshare bob's document from the household\?/i);

    // Confirm → DELETE to the admin route with the source pk.
    await ev.click(screen.getByTestId("confirm-force-unshare-files:42"));
    await waitFor(() => {
      const del = fetchImpl.mock.calls.filter(
        (c) =>
          String(c[0]).includes("/api/v1/admin/shared-items/files/42") &&
          (c[1] as RequestInit)?.method === "DELETE",
      );
      expect(del.length).toBe(1);
    });
    // List refetched after success (initial GET + invalidation GET ≥ 2).
    await waitFor(() => {
      const gets = fetchImpl.mock.calls.filter(
        (c) =>
          String(c[0]).endsWith("/api/v1/admin/shared-items") &&
          (c[1] as RequestInit)?.method === "GET",
      );
      expect(gets.length).toBeGreaterThanOrEqual(2);
    });
  });

  it("never renders the affordance for a non-admin member (mirrors the server gate)", async () => {
    const { client, tokens, fetchImpl } = setup({ me: MEMBER });
    renderView(client, tokens);

    // Once the member's identity resolves, the view returns null — nothing shown,
    // and crucially it never fetches the household shared-items list.
    await waitFor(() => expect(screen.queryByTestId("admin-shared-items")).toBeNull());
    const listGets = fetchImpl.mock.calls.filter(
      (c) =>
        String(c[0]).endsWith("/api/v1/admin/shared-items") &&
        (c[1] as RequestInit)?.method === "GET",
    );
    expect(listGets.length).toBe(0);
  });

  it("surfaces an error when the retract fails", async () => {
    const { client, tokens } = setup({ me: ADMIN, deleteStatus: 500 });
    const ev = userEvent.setup();
    renderView(client, tokens);

    await screen.findByText("insurance.pdf");
    await ev.click(screen.getByTestId("force-unshare-files:42"));
    await ev.click(screen.getByTestId("confirm-force-unshare-files:42"));

    // The human-authored message is surfaced, not the raw {"detail":{…}} blob.
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Couldn't fully unshare — please retry.");
    expect(alert.textContent ?? "").not.toContain("unshare_incomplete");
  });
});
