// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { CaptureInboxView } from "../../../src/features/capture/CaptureInboxView";
import { jsonResponse } from "../../helpers/jsonResponse";

const adminUser = { id: "u1", email: "a@home.lan", role: "admin" as const };

function row(id: string, status: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    status,
    capture_type: "text",
    title: null,
    text: `note ${id}`,
    url: null,
    last_error: null,
    attachment_count: 0,
    transcript_count: 0,
    created_at: "2026-07-10T10:00:00Z",
    updated_at: "2026-07-10T10:00:00Z",
    ...extra,
  };
}

// AuthProvider supplies the QueryClient (retry: false).
function renderInbox(fetchImpl: typeof fetch) {
  const store = new AccessTokenStore();
  const client = new ApiClient({ tokens: store, fetchImpl });
  render(
    <AuthProvider client={client} tokens={store} skipRefreshOnMount>
      <CaptureInboxView />
    </AuthProvider>,
  );
}

describe("CaptureInboxView", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("lists pending+failed rows, shows last_error, and commit drops the row", async () => {
    let indexCalls = 0;
    const fetchImpl = vi.fn(async (input, init) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/captures/p1/index") && init?.method === "POST") {
        indexCalls += 1;
        return jsonResponse(201, { ...row("p1", "indexed") });
      }
      if (u.includes("/api/v1/captures") && (!init?.method || init.method === "GET")) {
        // request must carry status=pending&status=failed
        expect(u).toContain("status=pending");
        expect(u).toContain("status=failed");
        return jsonResponse(200, {
          captures: [
            row("p1", "pending"),
            row("f1", "failed", { last_error: "index_memory_unavailable" }),
          ],
          total: 2,
          limit: 20,
          offset: 0,
        });
      }
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
    globalThis.fetch = fetchImpl;

    renderInbox(fetchImpl);

    expect(await screen.findByText("note p1")).toBeTruthy();
    // failed row shows its last_error
    expect(screen.getByText(/index_memory_unavailable/i)).toBeTruthy();

    const user = userEvent.setup();
    await user.click(screen.getByText("note p1"));
    const panel = screen.getByLabelText("Capture detail");
    await user.click(within(panel).getByRole("button", { name: "Commit to memory" }));

    await waitFor(() => expect(indexCalls).toBe(1));
    // row dropped via setQueryData — no longer listed, and no refetch needed
    await waitFor(() => expect(screen.queryByText("note p1")).toBeNull());
  });

  it("blocks Commit while there are unsaved edits (must Save first)", async () => {
    const fetchImpl = vi.fn(async (input, init) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/captures") && (!init?.method || init.method === "GET")) {
        return jsonResponse(200, {
          captures: [row("p1", "pending")],
          total: 1,
          limit: 20,
          offset: 0,
        });
      }
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
    globalThis.fetch = fetchImpl;

    renderInbox(fetchImpl);
    const user = userEvent.setup();
    await user.click(await screen.findByText("note p1"));
    const panel = screen.getByLabelText("Capture detail");
    const commit = within(panel).getByRole("button", { name: "Commit to memory" });
    expect((commit as HTMLButtonElement).disabled).toBe(false);
    // Edit the note → dirty → commit blocked until saved.
    await user.type(within(panel).getByLabelText("Note"), " edited");
    expect((commit as HTMLButtonElement).disabled).toBe(true);
  });

  it("failed row opens with a Retry button", async () => {
    const fetchImpl = vi.fn(async (input, init) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/captures") && (!init?.method || init.method === "GET")) {
        return jsonResponse(200, {
          captures: [row("f1", "failed", { last_error: "boom" })],
          total: 1,
          limit: 20,
          offset: 0,
        });
      }
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
    globalThis.fetch = fetchImpl;

    renderInbox(fetchImpl);
    const user = userEvent.setup();
    await user.click(await screen.findByText("note f1"));
    expect(screen.getByRole("button", { name: /Retry — add to memory/i })).toBeTruthy();
  });

  it("commit failure keeps the row and shows the mapped error", async () => {
    const fetchImpl = vi.fn(async (input, init) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/captures/p1/index") && init?.method === "POST") {
        return jsonResponse(503, { detail: { error: "index_memory_unavailable" } });
      }
      if (u.includes("/api/v1/captures") && (!init?.method || init.method === "GET")) {
        return jsonResponse(200, {
          captures: [row("p1", "pending")],
          total: 1,
          limit: 20,
          offset: 0,
        });
      }
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
    globalThis.fetch = fetchImpl;

    renderInbox(fetchImpl);
    const user = userEvent.setup();
    await user.click(await screen.findByText("note p1"));
    const panel = screen.getByLabelText("Capture detail");
    await user.click(within(panel).getByRole("button", { name: "Commit to memory" }));

    // mapped error shown; row still present (remove-on-success only)
    expect(await within(panel).findByText(/Memory search is temporarily unavailable/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /note p1/i })).toBeTruthy(); // card still listed
  });

  it("shows an empty state when there are no inbox captures", async () => {
    const fetchImpl = vi.fn(async (input, init) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/captures") && (!init?.method || init.method === "GET")) {
        return jsonResponse(200, { captures: [], total: 0, limit: 20, offset: 0 });
      }
      return jsonResponse(404, {});
    }) as unknown as typeof fetch;
    globalThis.fetch = fetchImpl;

    renderInbox(fetchImpl);
    expect(await screen.findByText(/Your inbox is empty/i)).toBeTruthy();
  });
});
