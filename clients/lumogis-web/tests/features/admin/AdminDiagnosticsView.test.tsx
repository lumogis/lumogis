// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// Admin diagnostics — GET /api/v1/admin/diagnostics (read-only).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { AdminDiagnosticsView } from "../../../src/features/admin/AdminDiagnosticsView";
import { jsonResponse } from "../../helpers/jsonResponse";

const adminUser = { id: "admin1", email: "a@home.lan", role: "admin" as const };

function diagPayload() {
  return {
    status: "ok" as const,
    generated_at: "2026-04-26T12:00:00Z",
    core: {
      auth_enabled: true,
      tool_catalog_enabled: false,
      core_version: "0.3.0rc1",
      mcp_enabled: true,
      mcp_auth_required: false,
    },
    stores: [
      { name: "postgres", status: "ok" as const, message: null },
      { name: "qdrant", status: "ok" as const, message: null },
      { name: "embedder", status: "ok" as const, message: null },
      { name: "graph", status: "not_configured" as const, message: "GRAPH_BACKEND is not falkordb" },
    ],
    capabilities: {
      total: 1,
      healthy: 1,
      unhealthy: 0,
      services: [
        {
          id: "lumogis-graph",
          status: "healthy" as const,
          healthy: true,
          version: "0.1.0",
          last_seen: "2026-04-26T12:00:00Z",
          tools: 2,
        },
      ],
    },
    tools: {
      total: 10,
      available: 8,
      unavailable: 2,
      by_source: { core: 5, mcp: 3, capability: 2 },
    },
    warnings: [{ code: "codegen_check_requires_live_core", message: "Web codegen check needs Core." }],
  };
}

describe("AdminDiagnosticsView", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("calls GET /api/v1/admin/diagnostics and renders summary", async () => {
    let diagUrl: string | null = null;
    const fetchImpl = vi.fn(async (input: RequestInfo, _init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/diagnostics") && !u.includes("credential-key-fingerprint")) {
        diagUrl = u;
        return jsonResponse(200, diagPayload());
      }
      if (u.includes("/api/v1/admin/diagnostics/credential-key-fingerprint")) {
        return jsonResponse(200, {
          current_key_version: 1,
          rows_by_key_version: { user: {}, household: {}, system: {} },
        });
      }
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <AdminDiagnosticsView />
      </AuthProvider>,
    );

    await waitFor(() => expect(diagUrl).toContain("/api/v1/admin/diagnostics"));
    expect(await screen.findByLabelText(/Overall status: ok/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Tool catalog total: 10/)).toBeInTheDocument();
    expect(screen.getByText("lumogis-graph")).toBeInTheDocument();
    expect(screen.getByText(/codegen_check_requires_live_core/)).toBeInTheDocument();
  });

  it("renders read-only — no restart or invoke controls", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/diagnostics") && !u.includes("fingerprint")) {
        return jsonResponse(200, diagPayload());
      }
      if (u.includes("credential-key-fingerprint")) {
        return jsonResponse(200, {
          current_key_version: 1,
          rows_by_key_version: { user: {}, household: {}, system: {} },
        });
      }
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <AdminDiagnosticsView />
      </AuthProvider>,
    );

    await screen.findByLabelText(/Overall status: ok/);
    expect(screen.queryByRole("button", { name: /restart|invoke|run tool|save/i })).toBeNull();
  });

  it("renders forbidden when diagnostics returns 403", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/diagnostics") && !u.includes("fingerprint")) {
        return jsonResponse(403, { detail: "forbidden" });
      }
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <AdminDiagnosticsView />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent(/Admin role required/i);
  });
});
