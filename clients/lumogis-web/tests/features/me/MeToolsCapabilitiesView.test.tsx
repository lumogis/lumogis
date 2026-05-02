// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// Read-only Tools & capabilities view — GET /api/v1/me/tools only.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { MeToolsCapabilitiesView } from "../../../src/features/me/MeToolsCapabilitiesView";
import { jsonResponse } from "../../helpers/jsonResponse";

describe("MeToolsCapabilitiesView", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  const user = { id: "u1", email: "u@home.lan", role: "user" as const };

  const samplePayload = {
    tools: [
      {
        name: "search_files",
        label: "Search files",
        description: "Search indexed files.",
        source: "core",
        transport: "llm_loop",
        origin_tier: "local",
        available: true,
        why_not_available: null,
        capability_id: null,
        connector: "filesystem-mcp",
        action_type: "search_files",
        permission_mode: "unknown",
        requires_credentials: false,
      },
      {
        name: "memory.search",
        label: "Memory search",
        description: "MCP tool",
        source: "mcp",
        transport: "mcp_surface",
        origin_tier: "mcp_only",
        available: true,
        why_not_available: null,
        capability_id: null,
        connector: null,
        action_type: null,
        permission_mode: "unknown",
        requires_credentials: false,
      },
      {
        name: "oop.x",
        label: "Oop X",
        description: "External",
        source: "capability",
        transport: "catalog_only",
        origin_tier: "capability_backed",
        available: false,
        why_not_available: "capability service not healthy (last probe failed)",
        capability_id: "svc.test",
        connector: null,
        action_type: null,
        permission_mode: "unknown",
        requires_credentials: false,
      },
    ],
    summary: {
      total: 3,
      available: 2,
      unavailable: 1,
      by_source: { capability: 1, core: 1, mcp: 1 },
    },
  };

  it("calls GET /api/v1/me/tools and renders summary", async () => {
    let toolsUrl: string | null = null;
    const fetchImpl = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/me/tools") && (init?.method === "GET" || init?.method === undefined)) {
        toolsUrl = u;
        return jsonResponse(200, samplePayload);
      }
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MeToolsCapabilitiesView />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByLabelText("Total tools: 3")).toBeInTheDocument();
    });
    expect(toolsUrl).toBeTruthy();
    expect(toolsUrl).toContain("/api/v1/me/tools");
    expect(screen.getByLabelText("Total tools: 3")).toBeInTheDocument();
    expect(screen.getByLabelText("Available tools: 2")).toBeInTheDocument();
    expect(screen.getByLabelText("Unavailable tools: 1")).toBeInTheDocument();
    const getCalls = fetchImpl.mock.calls.filter(
      (c) => String(c[0]).includes("/api/v1/me/tools") && (c[1] as RequestInit | undefined)?.method !== "POST",
    );
    expect(getCalls.length).toBeGreaterThan(0);
    expect(
      fetchImpl.mock.calls.some(
        (c) => String(c[0]).includes("/api/v1/me/tools") && (c[1] as RequestInit)?.method === "POST",
      ),
    ).toBe(false);
  });

  it("shows available and unavailable tools and the unavailable reason", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/me/tools")) return jsonResponse(200, samplePayload);
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MeToolsCapabilitiesView />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("Search files")).toBeInTheDocument();
    });
    expect(screen.getByText("Memory search")).toBeInTheDocument();
    expect(screen.getByLabelText("Tool status: unavailable")).toBeInTheDocument();
    expect(screen.getAllByLabelText("Tool status: available").length).toBe(2);
    expect(
      screen.getByText("capability service not healthy (last probe failed)"),
    ).toBeInTheDocument();
    expect(screen.getByText("svc.test")).toBeInTheDocument();
  });

  it("renders error state on failed load", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/me/tools")) return jsonResponse(500, { detail: "server oops" });
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MeToolsCapabilitiesView />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByRole("alert").textContent).toMatch(/server oops|500/i);
  });

  it("has no action or run buttons (read-only surface)", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, user);
      if (u.includes("/api/v1/me/tools")) return jsonResponse(200, samplePayload);
      return jsonResponse(404, {});
    });
    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <MeToolsCapabilitiesView />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("Search files")).toBeInTheDocument();
    });
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: /run/i })).toBeNull();
  });
});
