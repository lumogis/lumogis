// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// Phase 3.5 — AdminAuditView reverse error handling.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { AdminAuditView } from "../../../src/features/admin/AdminAuditView";
import { jsonResponse } from "../../helpers/jsonResponse";

function auditResponse(): Response {
  return jsonResponse(200, {
    audit: [
      {
        id: 1,
        action_name: "test.action",
        connector: "test",
        mode: "DO",
        input_summary: null,
        result_summary: "ok",
        reverse_token: "tok-1",
        reverse_action: { name: "reverse" },
        executed_at: "2026-04-24T00:00:00Z",
        reversed_at: null,
      },
    ],
  });
}

function setup(reverseResponse: Response) {
  const user = { id: "admin", email: "admin@home.lan", role: "admin" as const };
  const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/v1/auth/me")) return jsonResponse(200, user);
    if (url.includes("/api/v1/admin/users")) return jsonResponse(200, []);
    if (url.includes("/api/v1/audit?") && init?.method === "GET") return auditResponse();
    if (url.includes("/api/v1/audit/tok-1/reverse") && init?.method === "POST") return reverseResponse;
    return jsonResponse(404, { detail: "not found" });
  });
  const tokens = new AccessTokenStore();
  const client = new ApiClient({ tokens, fetchImpl: fetchImpl as unknown as typeof fetch });
  return { client, tokens, fetchImpl };
}

describe("AdminAuditView", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("invalidates the audit list after unknown reverse token", async () => {
    const { client, tokens, fetchImpl } = setup(
      jsonResponse(404, { detail: { error: "unknown_reverse_token" } }),
    );
    const user = userEvent.setup();

    render(
      <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
        <AdminAuditView />
      </AuthProvider>,
    );
    await screen.findByRole("button", { name: /^reverse$/i });
    await user.click(screen.getByRole("button", { name: /^reverse$/i }));

    await waitFor(() => {
      const auditGets = fetchImpl.mock.calls.filter(
        (c) => String(c[0]).includes("/api/v1/audit?") && (c[1] as RequestInit)?.method === "GET",
      );
      expect(auditGets.length).toBeGreaterThanOrEqual(2);
    });
    expect(screen.getByRole("status")).toHaveTextContent(/reverse token not found/i);
  });

  it("keeps the audit row in place after retryable reverse_failed", async () => {
    const { client, tokens, fetchImpl } = setup(
      jsonResponse(400, { detail: { error: "reverse_failed", detail: "temporary" } }),
    );
    const user = userEvent.setup();

    render(
      <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
        <AdminAuditView />
      </AuthProvider>,
    );
    await screen.findByRole("button", { name: /^reverse$/i });
    await user.click(screen.getByRole("button", { name: /^reverse$/i }));

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent(/reverse failed: temporary/i);
    });
    const auditGets = fetchImpl.mock.calls.filter(
      (c) => String(c[0]).includes("/api/v1/audit?") && (c[1] as RequestInit)?.method === "GET",
    );
    expect(auditGets).toHaveLength(1);
    expect(screen.getByRole("button", { name: /^reverse$/i })).toBeInTheDocument();
  });

  it("wraps the audit table in a bounded horizontal scroll region (Phase 2C)", async () => {
    const { client, tokens } = setup(jsonResponse(400, { detail: { error: "reverse_failed" } }));
    render(
      <AuthProvider client={client} tokens={tokens} skipRefreshOnMount>
        <AdminAuditView />
      </AuthProvider>,
    );
    const table = await screen.findByRole("table");
    expect(table.closest(".lumogis-table-scroll")).toBeTruthy();
    expect(table).toHaveClass("lumogis-dense-table");
  });
});
