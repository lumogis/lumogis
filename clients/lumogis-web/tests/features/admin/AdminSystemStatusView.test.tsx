// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
// Admin system status — GET /api/v1/admin/diagnostics/stack-status.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { ApiClient } from "../../../src/api/client";
import { AccessTokenStore } from "../../../src/api/tokens";
import { AuthProvider } from "../../../src/auth/AuthProvider";
import { AdminSystemStatusView } from "../../../src/features/admin/AdminSystemStatusView";
import { jsonResponse } from "../../helpers/jsonResponse";

const adminUser = { id: "admin1", email: "a@home.lan", role: "admin" as const };

function stackPayload() {
  return {
    meta: {
      generated_at: "2026-06-01T12:00:00Z",
      cache_age_sec: 5,
      stack_control_reachable: true,
      overall_status: "ok" as const,
    },
    services: [
      {
        id: "postgres",
        display_name: "Postgres",
        state: "healthy" as const,
        runtime_kind: "docker_compose" as const,
        runtime_detail: { compose_state: "running" },
        message: null,
      },
    ],
    storage: [
      {
        mount_id: "host_root",
        path_label: "Host root partition",
        partition_id: "1",
        used_bytes: 50,
        total_bytes: 100,
        used_percent: 50,
        warn_threshold_percent: 80,
        status: "ok" as const,
      },
    ],
    ollama: [{ name: "llama3", size_bytes: 1000, modified_at: null, loaded: true }],
    warnings: [],
  };
}

describe("AdminSystemStatusView", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("calls stack-status and renders services, storage, and ollama", async () => {
    let stackUrl: string | null = null;
    const fetchImpl = vi.fn(async (input: RequestInfo) => {
      const u = String(input);
      if (u.includes("/api/v1/auth/me")) return jsonResponse(200, adminUser);
      if (u.includes("/api/v1/admin/diagnostics/stack-status")) {
        stackUrl = u;
        return jsonResponse(200, stackPayload());
      }
      return jsonResponse(404, { detail: "not found" });
    });
    globalThis.fetch = fetchImpl as typeof fetch;

    const store = new AccessTokenStore();
    const client = new ApiClient({ tokens: store, fetchImpl: fetchImpl as unknown as typeof fetch });

    render(
      <AuthProvider client={client} tokens={store} skipRefreshOnMount>
        <AdminSystemStatusView />
      </AuthProvider>,
    );

    await waitFor(() => expect(stackUrl).toContain("/api/v1/admin/diagnostics/stack-status"));
    expect(await screen.findByText("System status")).toBeTruthy();
    expect(screen.getByText("Postgres")).toBeTruthy();
    expect(screen.getByText("Host root partition")).toBeTruthy();
    expect(screen.getByText("llama3")).toBeTruthy();
  });
});
